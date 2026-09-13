/**
 * LandingPage — the public home page (`/`, no auth guard).
 *
 * Layout borrows its structure from the three references (maritime.sh,
 * lantern.md, graphify.com): floating pill nav, oversized hero with an
 * inline ask box, bordered logo wall, left-text/right-visual split, a
 * 4-cell bordered feature grid, a centered "not a black box" trust band, a
 * graphify-style X-vs-check + report table, a live-answer demo strip, a
 * closing CTA, and a lantern-style mono footer.
 *
 * Theme: follows the whole-app theme (`theme-store` + `<html data-theme>`,
 * same as auth/chat). Dark is the art-directed default; light is a warm
 * paper variant of the same amber/ember system (see `landing.css`). Accent
 * is the loader's amber/ember pair (#ffbf48 / #be4a1d) — never purple. The
 * four supplied Uiverse elements are all used, re-skinned to that pair:
 *  - `GooLoader` — the gooey loader (andrew-manzyk), report + CTA decoration
 *  - `.bl-grid-bg` — the faint grid, hero backdrop
 *  - `.bl-ask` — the glowing conic-border input, hero ask box
 *  - `.bl-wave` — the loading bars, demo strip (amber, not blue)
 */
import { useId, useState } from 'react';
import type { FormEvent } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import {
  ArrowRight,
  ArrowUpRight,
  Briefcase,
  Calculator,
  Check,
  ClipboardList,
  Code2,
  Database,
  FileUp,
  Flag,
  Lock,
  Rocket,
  Search,
  ShieldCheck,
  ShoppingBag,
  Sparkles,
  Store,
  UploadCloud,
  User,
  UtensilsCrossed,
  X,
  Zap,
} from 'lucide-react';
import { GooLoader } from '../../components/GooLoader';
import { ThemeToggle } from '../../components/ThemeToggle';
import { useRevealRoot } from './useRevealRoot';
import './landing.css';

const SAMPLE_QUESTIONS = [
  'Why did revenue drop last week?',
  'Compare sales by region',
  'Forecast next quarter',
];

interface DemoExample {
  id: string;
  tab: string;
  title: string;
  question: string;
  answer: string;
  metricValue: string;
  metricLabel: string;
  bars: string[];
  tableHead: [string, string, string];
  tableRows: [string, string, string][];
  trust: string;
}

/* Hero demo — three example answer types behind tabs (diagnose a drop,
 * compare segments, project forward). Static mock data, clearly a preview;
 * each shows the same answer anatomy: chart, table, trust footer. */
const DEMO_EXAMPLES: DemoExample[] = [
  {
    id: 'revenue',
    tab: 'Revenue drop',
    title: 'revenue question',
    question: 'Why did revenue drop last week?',
    answer:
      'Revenue fell 7.4% week over week, driven by the West region and two paused wholesale accounts.',
    metricValue: '−7.4%',
    metricLabel: 'WoW revenue',
    bars: ['70%', '62%', '78%', '44%', '56%'],
    tableHead: ['Region', 'Revenue', 'WoW'],
    tableRows: [
      ['West', '$44.7k', '−12.1%'],
      ['East', '$51.3k', '−2.8%'],
    ],
    trust: 'Show the query · Confidence 70% · Flag this answer',
  },
  {
    id: 'compare',
    tab: 'Region compare',
    title: 'region comparison',
    question: 'Compare sales by region',
    answer:
      'East leads the quarter at $51.3k, 15% ahead of West. North holds flat while South slipped on one lost account.',
    metricValue: '+15%',
    metricLabel: 'East vs West',
    bars: ['88%', '72%', '58%', '40%', '64%'],
    tableHead: ['Region', 'Revenue', 'Share'],
    tableRows: [
      ['East', '$51.3k', '34%'],
      ['West', '$44.7k', '30%'],
      ['North', '$28.9k', '19%'],
    ],
    trust: 'Show the query · Confidence 74% · Flag this answer',
  },
  {
    id: 'forecast',
    tab: 'Forecast',
    title: 'forecast',
    question: 'Forecast next quarter',
    answer:
      'Pace suggests +12% quarter over quarter if West recovers to its 4-week average. A projection, not a fact.',
    metricValue: '+12%',
    metricLabel: 'Projected QoQ',
    bars: ['35%', '48%', '60%', '74%', '88%'],
    tableHead: ['Month', 'Projected', 'Growth'],
    tableRows: [
      ['Oct', '$58.2k', '+8%'],
      ['Nov', '$61.4k', '+11%'],
      ['Dec', '$66.0k', '+14%'],
    ],
    trust: 'Show the query · Confidence 62% · Flag this answer',
  },
];

export function LandingPage() {
  const navigate = useNavigate();
  const askId = useId();
  const [question, setQuestion] = useState('');
  const [demoTab, setDemoTab] = useState(0);
  const demo = DEMO_EXAMPLES[demoTab];
  const revealRef = useRevealRoot<HTMLElement>();

  const submitAsk = (event: FormEvent) => {
    event.preventDefault();
    // The question rides along to signup so a new user lands in the
    // chat with it intact (mirrors lantern's "comes with you" note).
    if (question.trim()) {
      try {
        sessionStorage.setItem('bl-pending-question', question.trim());
      } catch {
        /* storage unavailable — still route to signup */
      }
    }
    navigate('/signup');
  };

  return (
    <div className="bl-landing">
      {/* Floating pill nav — maritime / graphify pattern */}
      <header className="bl-navwrap">
        <nav className="bl-nav" aria-label="Primary">
          <Link to="/" className="bl-nav__brand">
            <span className="bl-logo-tile" aria-hidden="true">
              <img src="/logo.png" alt="" />
            </span>
            <span>BuildifyLabs</span>
          </Link>
          <div className="bl-nav__links">
            <a href="#product">Product</a>
            <a href="#how">How it works</a>
            <a href="#trust">Trust</a>
          </div>
          <div className="bl-nav__actions">
            <Link to="/signin" className="bl-nav__login">
              Log in
            </Link>
            <Link to="/signup" className="bl-nav__signup">
              Sign up <ArrowRight size={14} aria-hidden="true" />
            </Link>
            <ThemeToggle className="bl-nav__theme" />
          </div>
        </nav>
      </header>

      <main id="main" ref={revealRef}>
        {/* ---------- HERO ---------- */}
        <section className="bl-hero" aria-labelledby="bl-hero-title">
          <div className="bl-grid-bg" aria-hidden="true" />
          <div className="bl-hero__orbs" aria-hidden="true">
            <i className="bl-hero__orb bl-hero__orb--amber" />
            <i className="bl-hero__orb bl-hero__orb--ember" />
          </div>
          <div className="bl-hero__inner">
            <p className="bl-eyebrow">Upload CSV &rarr; Ask in English &rarr; Trusted answer</p>
            <h1 id="bl-hero-title" className="bl-hero__title">
              Ask your business data anything.
              <span className="bl-hero__accent"> Get answers you can trace.</span>
            </h1>
            <p className="bl-hero__sub">
              Upload a spreadsheet, ask in plain English, and get the right chart, the why
              behind it, and the exact query it came from. No dashboards to build. No SQL
              to write.
            </p>
            <div className="bl-hero__ctas">
              <Link to="/signup" className="bl-btn bl-btn--primary">
                Start free <ArrowRight size={16} aria-hidden="true" />
              </Link>
              <Link to="/app" className="bl-btn bl-btn--ghost">
                Open the app
              </Link>
            </div>

            {/* Glowing ask box — the supplied `#poda` element, amber-skinned.
                The halo lives inside `bl-ask__main` so it only ever covers
                the input itself, never the hint text or CTAs above. */}
            <form className="bl-ask" onSubmit={submitAsk} role="search" aria-label="Try a question">
              <div className="bl-ask__main">
                <div id="bl-ask-glow" className="bl-ask__halo" aria-hidden="true">
                  <div className="bl-ask__white" />
                  <div className="bl-ask__border" />
                  <div className="bl-ask__dark" />
                  <div className="bl-ask__glow" />
                </div>
                <Search size={18} className="bl-ask__search" aria-hidden="true" />
                <label htmlFor={askId} className="bl-ask__label">
                  Ask a business question
                </label>
                <input
                  id={askId}
                  className="bl-ask__input"
                  type="text"
                  value={question}
                  onChange={(event) => setQuestion(event.target.value)}
                  placeholder="Why did revenue drop last week?"
                  autoComplete="off"
                />
                <span className="bl-ask__mask" aria-hidden="true" />
                <button type="submit" className="bl-ask__submit">
                  Ask <ArrowUpRight size={16} aria-hidden="true" />
                </button>
              </div>
              <p className="bl-ask__hint">Free to start. Anything you type comes with you to the chat.</p>
            </form>

            <ul className="bl-hero__meta" aria-label="Highlights">
              <li>4 free questions</li>
              <li>No card to start</li>
              <li>Your data stays yours</li>
            </ul>

            {/* Product mock — maritime dashboard-poster slot, three
                tabbed example answers sharing one anatomy */}
            <div className="bl-mock bl-reveal">
              <div className="bl-mock__bar" aria-hidden="true">
                <span />
                <span />
                <span />
                <em>buildifylabs / app — {demo.title}</em>
              </div>
              <div className="bl-mock__tabs" role="tablist" aria-label="Example answers">
                {DEMO_EXAMPLES.map((example, index) => (
                  <button
                    key={example.id}
                    type="button"
                    role="tab"
                    id={`bl-demo-tab-${example.id}`}
                    aria-selected={index === demoTab}
                    aria-controls="bl-demo-panel"
                    className="bl-mock__tab"
                    onClick={() => setDemoTab(index)}
                  >
                    {example.tab}
                  </button>
                ))}
              </div>
              <div
                className="bl-mock__body"
                role="tabpanel"
                id="bl-demo-panel"
                aria-labelledby={`bl-demo-tab-${demo.id}`}
              >
                <p className="bl-mock__user">{demo.question}</p>
                <div className="bl-mock__answer">
                  <p>{demo.answer}</p>
                  <div className="bl-mock__row">
                    <div className="bl-mock__metric">
                      <span className="bl-mock__metric-value">{demo.metricValue}</span>
                      <span className="bl-mock__metric-label">{demo.metricLabel}</span>
                    </div>
                    <div className="bl-mock__bars" aria-hidden="true">
                      {demo.bars.map((height) => (
                        <i key={height} style={{ height }} />
                      ))}
                    </div>
                  </div>
                  <table className="bl-mock__table">
                    <thead>
                      <tr>
                        {demo.tableHead.map((head) => (
                          <th key={head}>{head}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {demo.tableRows.map(([first, second, third]) => (
                        <tr key={first}>
                          <td>{first}</td>
                          <td>{second}</td>
                          <td>{third}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  <p className="bl-mock__trust">
                    {demo.trust}
                  </p>
                </div>
              </div>
            </div>
          </div>
        </section>

        {/* ---------- AUDIENCE — honest replacement for a fake logo wall:
            who the product is for, never who supposedly uses it ---------- */}
        <section className="bl-audience bl-reveal" aria-label="Who BuildifyLabs is for">
          <p className="bl-audience__caption">Built for operators who live in spreadsheets</p>
          <ul className="bl-audience__grid">
            {[
              { icon: Rocket, label: 'D2C founders' },
              { icon: Store, label: 'Retail owners' },
              { icon: Briefcase, label: 'Agency leads' },
              { icon: Calculator, label: 'Finance teams' },
              { icon: ClipboardList, label: 'Ops managers' },
              { icon: User, label: 'Freelancers' },
              { icon: UtensilsCrossed, label: 'Restaurant groups' },
              { icon: ShoppingBag, label: 'Online sellers' },
            ].map(({ icon: Icon, label }) => (
              <li key={label} className="bl-audience__cell">
                <Icon size={20} aria-hidden="true" />
                <span>{label}</span>
              </li>
            ))}
          </ul>
          <p className="bl-audience__note">No data team required. If it fits in a CSV, you can ask it anything.</p>
        </section>

        {/* ---------- SPLIT — maritime "one agent per customer" ---------- */}
        <section id="product" className="bl-split" aria-labelledby="bl-split-title">
          <div className="bl-split__text bl-reveal">
            <p className="bl-eyebrow">Built for owners</p>
            <h2 id="bl-split-title">
              One upload per workspace.
              <span className="bl-hero__accent"> Answers from your data.</span>
            </h2>
            <p>
              Every upload lands in its own typed table. One question runs scoped SQL
              against it, computes the numbers in code, and narrates the result — tagged
              with your workspace, holding its own history, showing its own query.
            </p>
            <p className="bl-split__line">No shared tables. No other workspace&apos;s rows in your answer.</p>
            <a href="#how" className="bl-textlink">
              See how questions are answered <ArrowRight size={14} aria-hidden="true" />
            </a>
          </div>
          <div className="bl-pipeline bl-reveal" aria-label="How an answer is built">
            {[
              { icon: FileUp, title: 'Upload', body: 'CSV lands in your own table.' },
              { icon: Database, title: 'Scoped SQL', body: 'Reads only your rows.' },
              { icon: Zap, title: 'Stats in code', body: 'Averages, growth, ratios.' },
              { icon: Sparkles, title: 'Answer', body: 'Chart, why, and query.' },
            ].map(({ icon: Icon, title, body }) => (
              <div key={title} className="bl-pipeline__step">
                <Icon size={18} aria-hidden="true" />
                <p className="bl-pipeline__title">{title}</p>
                <p className="bl-pipeline__body">{body}</p>
              </div>
            ))}
          </div>
        </section>

        {/* ---------- 4-CELL GRID — maritime SDKs row ---------- */}
        <section className="bl-cells bl-reveal" aria-label="What you get">
          <p className="bl-eyebrow bl-eyebrow--center">Everything included</p>
          <div className="bl-cells__grid">
            {[
              { icon: UploadCloud, title: 'Upload anything', body: 'CSV today, with a clear processing → completed → failed status. 3MB free / 10MB pro.' },
              { icon: Lock, title: 'Scoped by design', body: 'One table per workspace. A generated query can never read another workspace\u2019s rows.' },
              { icon: Code2, title: 'Show the query', body: 'Every answer carries its SQL plus a raw data preview. Audit anything in one click.' },
              { icon: Flag, title: 'Flag anything', body: 'One tap flags a shaky answer into the review log — never hidden, never lost.' },
            ].map(({ icon: Icon, title, body }) => (
              <div key={title} className="bl-cells__cell">
                <Icon size={22} aria-hidden="true" />
                <h3>{title}</h3>
                <p>{body}</p>
              </div>
            ))}
          </div>
        </section>

        {/* ---------- TRUST BAND — maritime centered headline + lantern badges ---------- */}
        <section id="trust" className="bl-trust bl-reveal" aria-labelledby="bl-trust-title">
          <p className="bl-eyebrow bl-eyebrow--center">Why trust it</p>
          <h2 id="bl-trust-title">
            Not a black box. <span className="bl-hero__accent">A real query per answer.</span>
          </h2>
          <div className="bl-trust__grid">
            <article className="bl-trust__card">
              <h3>One question to a running answer.</h3>
              <p>
                BuildifyLabs reads your columns, writes scoped SQL, and puts a traced
                answer in front of you. No pipeline to wire up.
              </p>
            </article>
            <article className="bl-trust__card">
              <h3>Honest: 4 questions a window</h3>
              <p>
                Everyone gets 4 questions per 6-hour window and 100 lifetime on the free
                plan. Limits arrive as plain notices with a reset time — the input never
                locks you out.
              </p>
            </article>
            <article className="bl-trust__card">
              <h3>Fast: live in seconds</h3>
              <p>
                One question from the chat box to a charted answer. A cold server may
                take a minute to wake; everything after that is seconds.
              </p>
            </article>
          </div>
          <ul className="bl-badges" aria-label="Trust commitments">
            {[
              { icon: ShieldCheck, top: 'Scoped queries', bottom: 'Only your rows' },
              { icon: Code2, top: 'Show the query', bottom: 'SQL + raw rows' },
              { icon: Flag, top: 'Flag path', bottom: 'Every answer' },
            ].map(({ icon: Icon, top, bottom }) => (
              <li key={top} className="bl-badges__item">
                <Icon size={22} aria-hidden="true" />
                <span className="bl-badges__top">{top}</span>
                <span className="bl-badges__bottom">{bottom}</span>
              </li>
            ))}
          </ul>
        </section>

        {/* ---------- REPORT — graphify X-vs-check + report table ---------- */}
        <section id="how" className="bl-report" aria-labelledby="bl-report-title">
          <div className="bl-report__left bl-reveal">
            <p className="bl-eyebrow">Why questions, not dashboards</p>
            <h2 id="bl-report-title">Every answer traces to a real path.</h2>
            <p className="bl-report__sub">
              Dashboard clicks and fuzzy chat guesses make your copilot guess. A scoped
              query plus computed stats gives it structure to reason over.
            </p>
            <ul className="bl-compare">
              {[
                { bad: 'Dashboards show what happened and stop there.', good: 'BuildifyLabs narrates the why — hedged, ranked, and cited.' },
                { bad: 'Chatbots guess from fuzzy text and hope.', good: 'We run scoped SQL and compute the numbers in code first.' },
                { bad: 'Answers backed by an opaque score.', good: 'Answers backed by SQL you can audit, tagged as fact or possible factor.' },
              ].map(({ bad, good }) => (
                <li key={bad} className="bl-compare__row">
                  <span className="bl-compare__bad">
                    <X size={14} aria-hidden="true" /> {bad}
                  </span>
                  <span className="bl-compare__good">
                    <Check size={14} aria-hidden="true" /> {good}
                  </span>
                </li>
              ))}
            </ul>
            <div className="bl-doctable" aria-label="Answer report contents">
              <p className="bl-doctable__file">ANSWER_REPORT.MD</p>
              <dl>
                <div>
                  <dt>Top metrics</dt>
                  <dd>The numbers that moved, ranked. Watch these first.</dd>
                </div>
                <div>
                  <dt>Hidden drivers</dt>
                  <dd>Cross-column causes you didn&apos;t know to check.</dd>
                </div>
                <div>
                  <dt>The &ldquo;why&rdquo;</dt>
                  <dd>Possible factors — never stated as fact — with context.</dd>
                </div>
              </dl>
            </div>
          </div>
          <div className="bl-report__right bl-reveal">
            <GooLoader label="Decorative amber orb showing the product glow" />
            {/* Loading wave — the supplied bars, amber-skinned */}
            <div className="bl-wave" role="img" aria-label="Answer streaming indicator">
              {Array.from({ length: 5 }).map((_, i) => (
                <div key={i} className="bl-wave__bar" aria-hidden="true" />
              ))}
            </div>
            <div className="bl-confidence">
              <div className="bl-confidence__row">
                <span>Confidence</span>
                <span>70%</span>
              </div>
              <div className="bl-confidence__meter" aria-hidden="true">
                <i style={{ width: '70%' }} />
              </div>
              <p>Bounded 0–1, shown only when it means something.</p>
            </div>
          </div>
        </section>

        {/* ---------- DEMO — chat-screenshot strip ---------- */}
        <section className="bl-demo bl-reveal" aria-labelledby="bl-demo-title">
          <p className="bl-eyebrow bl-eyebrow--center">How it works</p>
          <h2 id="bl-demo-title">From zero to a traced answer in about a minute.</h2>
          <ol className="bl-steps">
            <li>
              <span className="bl-steps__n">01</span>
              <p><strong>Upload.</strong> Drop a CSV — it lands in your own table.</p>
            </li>
            <li>
              <span className="bl-steps__n">02</span>
              <p><strong>Ask.</strong> Pick a starter or type your own question.</p>
            </li>
            <li>
              <span className="bl-steps__n">03</span>
              <p><strong>Trace.</strong> Open the query, check confidence, flag iffy bits.</p>
            </li>
          </ol>
          <ul className="bl-chips" aria-label="Starter questions">
            {SAMPLE_QUESTIONS.map((q) => (
              <li key={q}>
                <button type="button" className="bl-chips__chip" onClick={() => setQuestion(q)}>
                  {q}
                </button>
              </li>
            ))}
          </ul>
        </section>

        {/* ---------- CTA — fox split ---------- */}
        <section className="bl-cta bl-cta--split bl-reveal" aria-labelledby="bl-cta-title">
          <div className="bl-cta__art" role="img" aria-label="BuildifyLabs fox working on a laptop beside books about graphs, sales, and statistics">
            <img src="/login.png" alt="" loading="lazy" />
          </div>
          <div className="bl-cta__copy">
            <h2 id="bl-cta-title">Upload your first CSV. Ask your first question.</h2>
            <p>Start from a blank chat and get a traced answer in about a minute. Stay free until it earns a habit.</p>
            <div className="bl-hero__ctas">
              <Link to="/signup" className="bl-btn bl-btn--primary">
                Start free <ArrowRight size={16} aria-hidden="true" />
              </Link>
              <Link to="/app" className="bl-btn bl-btn--ghost">
                Open the app
              </Link>
            </div>
          </div>
        </section>
      </main>

      {/* ---------- FOOTER — lantern mono pattern ---------- */}
      <footer className="bl-footer">
        <div className="bl-footer__grid">
          <div>
            <p className="bl-footer__brand">
              <span className="bl-logo-tile" aria-hidden="true">
                <img src="/logo.png" alt="" />
              </span>{' '}
              BuildifyLabs
            </p>
            <p className="bl-footer__tag">End-to-end AI<br />BI copilot</p>
            <p className="bl-footer__backed">Free to start · 4 questions per window</p>
          </div>
          <nav aria-label="Product">
            <p className="bl-footer__head">Product</p>
            <a href="#product">Product</a>
            <a href="#how">How it works</a>
            <a href="#trust">Trust</a>
            <Link to="/app">Open the app</Link>
          </nav>
          <div>
            <p className="bl-footer__head">Trust &amp; security</p>
            <ul className="bl-footer__trust">
              <li><ShieldCheck size={16} aria-hidden="true" /> Scoped queries only</li>
              <li><Code2 size={16} aria-hidden="true" /> SQL on every answer</li>
              <li><Search size={16} aria-hidden="true" /> Export or delete anytime</li>
            </ul>
          </div>
          <nav aria-label="Account">
            <p className="bl-footer__head">Get started</p>
            <Link to="/signup">Sign up</Link>
            <Link to="/signin">Log in</Link>
            <Link to="/signup">Start free</Link>
          </nav>
        </div>
        <div className="bl-footer__bar">
          <p>© 2026 BuildifyLabs</p>
          <p className="bl-footer__note">Answers trace to real queries</p>
        </div>
      </footer>
    </div>
  );
}
