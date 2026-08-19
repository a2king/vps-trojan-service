import { useEffect } from 'react'

export function HomePage() {
  useEffect(() => {
    document.title = 'Northline Studio'
  }, [])

  return (
    <div className="home">
      <header className="home-bar">
        <div className="brand">
          <svg className="brand-mark" viewBox="0 0 32 32" fill="none" aria-hidden="true">
            <path d="M8 22.5L16 7.5L24 22.5H8Z" stroke="currentColor" strokeWidth="1.6" />
            <circle cx="16" cy="16.2" r="1.4" fill="currentColor" />
          </svg>
          Northline Studio
        </div>
        <nav className="home-nav">
          <a href="#work">Work</a>
          <a href="#about">Studio</a>
          <a href="#contact">Contact</a>
        </nav>
      </header>

      <section className="hero">
        <div className="eyebrow">Independent practice · Est. 2014</div>
        <h1>Quiet rooms for loud ideas.</h1>
        <p>
          Northline is a small studio working across identity, editorial design and spatial
          graphics. We collaborate with publishers, cultural institutions and family-owned
          companies that prefer lasting work over seasonal noise.
        </p>
      </section>

      <div className="stats">
        <div>
          <strong>12 yr</strong>
          <span>of independent practice</span>
        </div>
        <div>
          <strong>40+</strong>
          <span>published identities</span>
        </div>
        <div>
          <strong>6</strong>
          <span>people in the studio</span>
        </div>
      </div>

      <section className="section" id="work">
        <h2>Selected work</h2>
        <div className="work-grid">
          <article className="card tall">
            <small>Editorial · 2025</small>
            <b>Harbor Review, Issue 09</b>
          </article>
          <article className="card">
            <small>Identity · 2024</small>
            <b>Atelier Mora</b>
          </article>
          <article className="card">
            <small>Exhibition · 2023</small>
            <b>North Window</b>
          </article>
        </div>
      </section>

      <section className="section" id="about">
        <div className="about">
          <h2>The studio</h2>
          <div>
            <p>
              We work slowly, with a small bench and a preference for materials that age well:
              uncoated papers, metal type textures, and colour palettes drawn from coastal
              light rather than screens.
            </p>
            <p>
              Projects typically begin with a conversation and a stack of references. If that
              sounds like a fit, write to us with a short brief and a preferred timeline.
            </p>
          </div>
        </div>
      </section>

      <footer className="contact" id="contact">
        <div>Northline Studio · 18 Dock Lane</div>
        <div>
          Studio enquiries:{' '}
          <a href="mailto:studio@northline.example">studio@northline.example</a>
        </div>
        <div>© {new Date().getFullYear()} Northline</div>
      </footer>
    </div>
  )
}
