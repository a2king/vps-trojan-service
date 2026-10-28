from __future__ import annotations

import unittest
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import Account, AccountGroupVps, TrojanUser, Vps
from app.mysql_bridge import handle_trojan_sql
from app.services.nodes import bind_vps_to_proxy_groups, replace_group_nodes
from app.services.replica_sync import push_online_replicas
from app.timeutil import now_local


PASSWORD = "a" * 56
SELECT_SQL = f"SELECT quota, download + upload FROM users WHERE password = '{PASSWORD}'"


def _engine():
    return create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


def _session(engine) -> Session:
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)()


def _add_account(db: Session, name: str = "u1", enabled: int = 1) -> Account:
    account = Account(
        name=name,
        password="plain",
        password_sha224=PASSWORD if name == "u1" else (name[0] * 56),
        enabled=enabled,
        remark="",
        created_at=now_local(),
    )
    db.add(account)
    db.flush()
    return account


def _add_vps(db: Session, vps_id: str, *, replica: int = 0, status: str = "online", last_error: str = "") -> Vps:
    row = Vps(
        id=vps_id,
        name=vps_id,
        ssh_host="127.0.0.1",
        ssh_port=22,
        ssh_user="root",
        is_local=0,
        replica=replica,
        hidden=0,
        status=status,
        last_error=last_error,
        created_at=now_local(),
    )
    db.add(row)
    db.flush()
    return row


class BindVpsToProxyGroupsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = _engine()
        self.db = _session(self.engine)
        self.account = _add_account(self.db)
        self.old = _add_vps(self.db, "old.example.com")
        self.db.add(AccountGroupVps(account_id=self.account.id, group_name="PROXY", vps_id=self.old.id))
        self.db.add(AccountGroupVps(account_id=self.account.id, group_name="PROXY-VIDEO", vps_id=self.old.id))
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()

    def _ids(self, group: str) -> set[str]:
        rows = self.db.execute(
            select(AccountGroupVps.vps_id).where(
                AccountGroupVps.account_id == self.account.id,
                AccountGroupVps.group_name == group,
            )
        ).all()
        return {str(item[0]) for item in rows}

    def test_bind_writes_new_vps_into_existing_proxy_groups(self) -> None:
        fresh = _add_vps(self.db, "new.example.com")
        self.db.commit()
        bound = bind_vps_to_proxy_groups(self.db, fresh.id)
        self.assertIn("PROXY", bound)
        self.assertIn("PROXY-VIDEO", bound)
        self.assertEqual(self._ids("PROXY"), {self.old.id, fresh.id})
        self.assertEqual(self._ids("PROXY-VIDEO"), {self.old.id, fresh.id})
        self.assertEqual(self._ids("PROXY-AI"), set())

    def test_apply_groups_limits_binding(self) -> None:
        fresh = _add_vps(self.db, "limited.example.com")
        self.db.commit()
        bind_vps_to_proxy_groups(self.db, fresh.id, ["PROXY"])
        self.assertEqual(self._ids("PROXY"), {self.old.id, fresh.id})
        self.assertEqual(self._ids("PROXY-VIDEO"), {self.old.id})

    def test_skip_accounts_without_existing_groups(self) -> None:
        lonely = _add_account(self.db, "lonely")
        fresh = _add_vps(self.db, "fresh.example.com")
        self.db.commit()
        bind_vps_to_proxy_groups(self.db, fresh.id)
        rows = self.db.execute(
            select(AccountGroupVps.id).where(AccountGroupVps.account_id == lonely.id)
        ).all()
        self.assertEqual(rows, [])

    def test_empty_apply_groups_binds_nothing(self) -> None:
        fresh = _add_vps(self.db, "empty.example.com")
        self.db.commit()
        self.assertEqual(bind_vps_to_proxy_groups(self.db, fresh.id, []), [])
        self.assertEqual(self._ids("PROXY"), {self.old.id})


class ReplicaPushTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = _engine()
        self.db = _session(self.engine)
        self.account = _add_account(self.db)
        self.old = _add_vps(self.db, "old.example.com")
        self.replica = _add_vps(self.db, "replica.example.com", replica=1, status="online")
        self.db.add(AccountGroupVps(account_id=self.account.id, group_name="PROXY", vps_id=self.old.id))
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()

    def test_replace_group_nodes_then_push_writes_last_error(self) -> None:
        replace_group_nodes(self.db, self.account.id, [("PROXY", [self.old.id, self.replica.id])])
        with (
            patch("app.services.replica_sync.backup_sqlite"),
            patch("app.services.replica_sync.push_replica", side_effect=RuntimeError("ssh down")),
        ):
            errors = push_online_replicas(self.db)
        self.assertEqual(errors, [(self.replica.id, "ssh down")])
        self.db.refresh(self.replica)
        self.assertEqual(self.replica.last_error, "ssh down")

    def test_successful_push_clears_last_error(self) -> None:
        self.replica.last_error = "old"
        self.db.commit()
        with (
            patch("app.services.replica_sync.backup_sqlite"),
            patch("app.services.replica_sync.push_replica"),
        ):
            errors = push_online_replicas(self.db)
        self.assertEqual(errors, [])
        self.db.refresh(self.replica)
        self.assertEqual(self.replica.last_error, "")


class MysqlBridgeAuthLogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = _engine()
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)()
        self.account = _add_account(self.db)
        self.local = _add_vps(self.db, "example.com")
        self.node = _add_vps(self.db, "edge.example.com")
        self.db.add(AccountGroupVps(account_id=self.account.id, group_name="PROXY", vps_id=self.local.id))
        self.db.add(TrojanUser(username="u1", password=PASSWORD, quota=-1, download=0, upload=0))
        self.db.commit()
        import app.mysql_bridge as bridge

        self.bridge = bridge
        self._orig = bridge.engine
        bridge.engine = self.engine

    def tearDown(self) -> None:
        self.bridge.engine = self._orig
        self.db.close()

    def test_acl_denial_logs_denied_by_node_acl(self) -> None:
        with self.assertLogs("trojan-admin", level="INFO") as captured:
            rows, cols = handle_trojan_sql(SELECT_SQL, "edge.example.com")
        self.assertEqual(rows, [])
        self.assertEqual(cols, ["quota", "download + upload"])
        text = "\n".join(captured.output)
        self.assertIn("denied by node acl", text)
        self.assertNotIn("incorrect password", text)

    def test_unknown_password_logs_incorrect_password(self) -> None:
        unknown = "b" * 56
        sql = f"SELECT quota, download + upload FROM users WHERE password = '{unknown}'"
        with self.assertLogs("trojan-admin", level="INFO") as captured:
            rows, _ = handle_trojan_sql(sql, "example.com")
        self.assertEqual(rows, [])
        text = "\n".join(captured.output)
        self.assertIn("incorrect password", text)
        self.assertNotIn("denied by node acl", text)

    def test_assigned_node_allows_without_acl_log(self) -> None:
        rows, cols = handle_trojan_sql(SELECT_SQL, "example.com")
        self.assertEqual(rows, [(-1, 0)])
        self.assertEqual(cols, ["quota", "download + upload"])


if __name__ == "__main__":
    unittest.main()
