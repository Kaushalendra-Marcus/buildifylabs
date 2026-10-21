/**
 * LandingPage — public home page (`/`, no auth guard).
 *
 * Art direction follows the clean, soft-landing reference: a lilac wash,
 * oversized centered display type, rounded full-width panels, floating
 * product cards, pill badges, and a lot of whitespace. All copy is
 * BuildifyLabs': upload a spreadsheet, ask in plain English, and get a
 * chart, the why, and the exact query behind it.
 *
 * Behavior:
 * - the hero ask box carries a typed question to signup (`bl-pending-question`)
 * - starter chips fill the ask box
 * - example-answer tabs switch between four real answer layouts
 * - scroll reveals via `useRevealRoot`
 * - the whole page follows the app theme (`<html data-theme>`)
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
  FileUp,
  Flag,
  Lock,
  MessageSquare,
  Rocket,
  Search,
  ShieldCheck,
  ShoppingBag,
  Sparkles,
  Star,
  Store,
  UploadCloud,
  User,
  UtensilsCrossed,
} from 'lucide-react';
import { ThemeToggle } from '../../components/ThemeToggle';
import { useRevealRoot } from './useRevealRoot';
import './landing.css';

const SAMPLE_QUESTIONS = [
  'Why did revenue drop last week?',
  'Compare sales by region',
  'Forecast next quarter',
];

const AUDIENCE = [
  { icon: Rocket, label: 'D2C founders' },
  { icon: Store, label: 'Retail owners' },
  { icon: Briefcase, label: 'Agency leads' },
  { icon: Calculator, label: 'Finance teams' },
  { icon: ClipboardList, label: 'Ops managers' },
  { icon: User, label: 'Freelancers' },
  { icon: UtensilsCrossed, label: 'Restaurant groups' },
  { icon: ShoppingBag, label: 'Online sellers' },
];

interface DemoExample {
  id: 'revenue' | 'compare' | 'forecast' | 'channels';
  tab: string;
  question: string;
  answer: string;
  confidence: string;
}

/* One tab per answer shape: diagnosis (metric + bars + table), a
 * head-to-head comparison (versus cards + share bars), a forecast (trend
 * line + hedged factors), and a channel mix (donut + legend). Static
 * example data, clearly a preview of the product. */
const DEMO_EXAMPLES: DemoExample[] = [
  {
    id: 'revenue',
    tab: 'Revenue drop',
    question: 'Why did revenue drop last week?',
    answer:
      'Revenue fell 7.4% week over week, driven by the West region and two paused wholesale accounts.',
    confidence: 'Confidence 70%',
  },
  {
    id: 'compare',
    tab: 'Region compare',
    question: 'Compare sales by region',
    answer:
      'East leads the quarter at $51.3k, 15% ahead of West. North holds flat while South slipped on one lost account.',
    confidence: 'Confidence 74%',
  },
  {
    id: 'forecast',
    tab: 'Forecast',
    question: 'Forecast next quarter',
    answer:
      'Pace suggests +12% quarter over quarter if West recovers to its 4-week average. A projection, not a fact.',
    confidence: 'Confidence 62%',
  },
  {
    id: 'channels',
    tab: 'Channel mix',
    question: 'Where do sales come from?',
    answer:
      'Online drives 46% of revenue at $69.0k, ahead of retail at $48.0k. Wholesale trails while two accounts stay paused.',
    confidence: 'Confidence 71%',
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
    // chat with it intact.
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
      <header className="bl-navwrap">
        <nav className="bl-nav bl-shell" aria-label="Primary">
          <Link to="/" className="bl-brand" aria-label="BuildifyLabs home">
            <span className="bl-brand__mark" aria-hidden="true">
              <img src="/logo.png" alt="" />
            </span>
            <span className="bl-brand__name">BuildifyLabs</span>
          </Link>

          <div className="bl-nav__links">
            <a href="#product">Product</a>
            <a href="#how">How it works</a>
            <a href="#trust">Trust</a>
          </div>

          <div className="bl-nav__actions">
            <ThemeToggle className="bl-nav__theme" />
            <Link to="/signin" className="bl-nav__login">
              Log in
            </Link>
            <Link to="/signup" className="bl-btn bl-btn--solid bl-btn--sm">
              Start free <ArrowUpRight size={15} aria-hidden="true" />
            </Link>
          </div>
        </nav>
      </header>

      <main id="main" ref={revealRef}>
        {/* ---------- HERO ---------- */}
        <section className="bl-hero" aria-labelledby="bl-hero-title">
          <div className="bl-hero__aurora" aria-hidden="true">
            <i />
            <i />
            <i />
          </div>

          <div className="bl-hero__inner">
            <p className="bl-pill bl-pill--accent">
              No credit card required
              <span className="bl-pill__sep" aria-hidden="true" />
              4 free questions
              <ArrowRight size={14} aria-hidden="true" />
            </p>

            <h1 id="bl-hero-title" className="bl-hero__title">
              Ask your business data{' '}
              <span className="bl-hero__grad">anything.</span>
            </h1>

            <p className="bl-hero__sub">
              Upload a spreadsheet, ask in plain English, and get the right chart,
              the why behind it, and the exact query it came from.
            </p>

            <form
              className="bl-ask"
              onSubmit={submitAsk}
              role="search"
              aria-label="Try a question"
            >
              <div className="bl-ask__box">
                <Search size={18} className="bl-ask__icon" aria-hidden="true" />
                <label htmlFor={askId} className="bl-sr-only">
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
                <button type="submit" className="bl-btn bl-btn--solid">
                  Ask <ArrowUpRight size={16} aria-hidden="true" />
                </button>
              </div>

              <div className="bl-ask__try">
                <span className="bl-ask__try-label">Try</span>
                {SAMPLE_QUESTIONS.map((sample) => (
                  <button
                    key={sample}
                    type="button"
                    className="bl-chip"
                    onClick={() => setQuestion(sample)}
                  >
                    {sample}
                  </button>
                ))}
              </div>

              <p className="bl-hero__note">
                4 free questions <i aria-hidden="true" /> No card to start{' '}
                <i aria-hidden="true" /> Your data stays yours
              </p>
            </form>

            {/* Product preview — a browser window with three floating
                fragments around it (query, metric, confidence). */}
            <div className="bl-stage" aria-hidden="true">
              <div className="bl-stage__glow" />

              <div className="bl-float bl-float--query">
                <p className="bl-float__kicker">
                  <Code2 size={13} /> Query traced
                </p>
                <pre>{`SELECT region,\n       SUM(revenue)\nFROM q3_sales\nGROUP BY region;`}</pre>
                <p className="bl-float__foot">
                  <ShieldCheck size={12} /> Only your rows
                </p>
              </div>

              <div className="bl-app">
                <div className="bl-app__chrome">
                  <span className="bl-app__dots">
                    <i />
                    <i />
                    <i />
                  </span>
                  <span className="bl-app__url">
                    <Lock size={10} /> app.buildifylabs.in
                  </span>
                  <span className="bl-app__quota">4 of 4 questions left</span>
                </div>

                <div className="bl-app__body">
                  <aside className="bl-app__rail">
                    <span className="bl-app__rail-brand">
                      <img src="/logo.png" alt="" /> Buildify
                    </span>
                    <span className="bl-app__new">+ New chat</span>
                    <span className="bl-app__rail-label">Today</span>
                    <span className="bl-app__rail-item is-active">
                      Why did revenue drop…
                    </span>
                    <span className="bl-app__rail-item">Compare sales by region</span>
                    <span className="bl-app__rail-item">Forecast next quarter</span>
                  </aside>

                  <div className="bl-app__main">
                    <div className="bl-app__topbar">
                      <span>New chat</span>
                      <span className="bl-app__avatar">
                        <img src="/logo.png" alt="" />
                      </span>
                    </div>

                    <div className="bl-app__thread">
                      <p className="bl-app__user">Why did revenue drop last week?</p>

                      <div className="bl-app__assistant">
                        <span className="bl-app__who">
                          <img src="/logo.png" alt="" />
                          Buildify Intelligence
                        </span>
                        <p>
                          Revenue fell <strong>7.4%</strong> week over week —
                          driven by the West region and two paused wholesale
                          accounts.
                        </p>

                        <div className="bl-app__chart">
                          <div className="bl-app__chart-head">
                            <span>Revenue by week</span>
                            <span>Last 5 weeks</span>
                          </div>
                          <div className="bl-app__bars">
                            {[
                              { label: 'W1', value: '$55.8k', height: 62 },
                              { label: 'W2', value: '$52.4k', height: 74 },
                              { label: 'W3', value: '$59.1k', height: 68 },
                              { label: 'W4', value: '$44.7k', height: 88 },
                              { label: 'W5', value: '$48.9k', height: 48, down: true },
                            ].map(({ label, value, height, down }) => (
                              <span
                                key={label}
                                className={`bl-app__bar${down ? ' is-down' : ''}`}
                                title={`${label} · ${value}`}
                              >
                                <i style={{ height: `${height}%` }} />
                                <em>{label}</em>
                              </span>
                            ))}
                          </div>
                        </div>

                        <div className="bl-app__trust">
                          <span>
                            <Code2 size={11} /> Show query
                          </span>
                          <span>
                            <ShieldCheck size={11} /> Confidence 70%
                          </span>
                          <span>
                            <Flag size={11} /> Flag
                          </span>
                        </div>
                      </div>

                      <div className="bl-app__composer">
                        <span>Ask a follow-up…</span>
                        <i>
                          <ArrowUpRight size={13} />
                        </i>
                      </div>
                    </div>
                  </div>
                </div>
              </div>

              <div className="bl-float bl-float--metric">
                <p className="bl-float__kicker">Revenue · WoW</p>
                <strong>−7.4%</strong>
                <p className="bl-float__meta">
                  <span className="bl-dot bl-dot--down" /> West led the drop
                </p>
              </div>

              <div className="bl-float bl-float--confidence">
                <span
                  className="bl-ring"
                  style={{
                    background:
                      'conic-gradient(var(--bl-accent) 0 70%, var(--bl-track) 70% 100%)',
                  }}
                >
                  <span>70%</span>
                </span>
                <span>
                  <span className="bl-float__kicker">Confidence</span>
                  <span className="bl-float__meta">SQL traced · 12 rows</span>
                </span>
              </div>
            </div>
          </div>
        </section>

        {/* ---------- AUDIENCE ---------- */}
        <section className="bl-audience bl-reveal" aria-label="Who BuildifyLabs is for">
          <p className="bl-audience__caption">
            Built for operators who live in spreadsheets
          </p>
          <ul className="bl-audience__grid">
            {AUDIENCE.map(({ icon: Icon, label }) => (
              <li key={label}>
                <Icon size={16} aria-hidden="true" />
                <span>{label}</span>
              </li>
            ))}
          </ul>
          <p className="bl-audience__note">
            No data team required. If it fits in a CSV, you can ask it anything.
          </p>
        </section>

        {/* ---------- HOW IT WORKS ---------- */}
        <section id="how" className="bl-band bl-how bl-reveal">
          <div className="bl-band__inner">
            <div className="bl-band__head">
              <p className="bl-pill">
                <Sparkles size={14} aria-hidden="true" /> How it works
              </p>
              <h2>From spreadsheet to answer, without the busywork.</h2>
              <p className="bl-band__sub">
                No dashboards to build. No SQL to write. One upload, one
                question in plain English, and the whole trail behind the answer.
              </p>
            </div>

            <div className="bl-how__grid">
              <article className="bl-step">
                <div className="bl-mini bl-mini--upload">
                  <div className="bl-file">
                    <span className="bl-file__icon">
                      <FileUp size={17} aria-hidden="true" />
                    </span>
                    <span className="bl-file__meta">
                      <strong>q3-sales.csv</strong>
                      <em>2.4 MB · 1,842 rows</em>
                    </span>
                    <span className="bl-file__status">Ready</span>
                  </div>
                  <div className="bl-upload-zone">
                    <UploadCloud size={15} aria-hidden="true" />
                    Drop another file
                  </div>
                </div>
                <div className="bl-step__copy">
                  <span className="bl-step__num">Step 01</span>
                  <h3>Upload your data</h3>
                  <p>
                    CSV and XLSX land in a typed table that holds only your rows.
                    PDFs are chunked and searchable per question.
                  </p>
                </div>
              </article>

              <article className="bl-step">
                <div className="bl-mini bl-mini--ask">
                  <p className="bl-mini__bubble">Why did revenue drop last week?</p>
                  <div className="bl-mini__chips">
                    <span>Compare regions</span>
                    <span>Forecast Q4</span>
                  </div>
                  <span className="bl-mini__who">
                    <img src="/logo.png" alt="" /> Buildify is thinking
                    <i className="bl-typing">
                      <b />
                      <b />
                      <b />
                    </i>
                  </span>
                </div>
                <div className="bl-step__copy">
                  <span className="bl-step__num">Step 02</span>
                  <h3>Ask in plain English</h3>
                  <p>
                    Buildify reads your columns, writes schema-aware SQL, and
                    runs it against your workspace only. No query language
                    required.
                  </p>
                </div>
              </article>

              <article className="bl-step">
                <div className="bl-mini bl-mini--trace">
                  <div className="bl-mini__bars" aria-hidden="true">
                    {[46, 62, 54, 78, 40].map((height, index) => (
                      <i
                        key={height}
                        className={index === 4 ? 'is-down' : ''}
                        style={{ height: `${height}%` }}
                      />
                    ))}
                  </div>
                  <div className="bl-mini__trace-row">
                    <span>
                      <Code2 size={12} aria-hidden="true" /> Show the query
                    </span>
                    <span className="bl-mini__confidence">70%</span>
                  </div>
                </div>
                <div className="bl-step__copy">
                  <span className="bl-step__num">Step 03</span>
                  <h3>Trace the answer</h3>
                  <p>
                    Every answer carries its chart, the why behind it, and the
                    exact query — with confidence, citations, and a flag path.
                  </p>
                </div>
              </article>
            </div>
          </div>
        </section>

        {/* ---------- FEATURES ---------- */}
        <section id="product" className="bl-band bl-features bl-reveal">
          <div className="bl-band__inner">
            <div className="bl-band__head">
              <p className="bl-pill">
                <Star size={14} aria-hidden="true" /> Why BuildifyLabs
              </p>
              <h2>Answers you can check, not just trust.</h2>
              <p className="bl-band__sub">
                A business copilot should show its work. Every part of
                BuildifyLabs is built to make the answer auditable.
              </p>
            </div>

            <div className="bl-features__grid">
              <article className="bl-feature">
                <span className="bl-feature__icon">
                  <Lock size={21} aria-hidden="true" />
                </span>
                <h3>Scoped by design</h3>
                <p>
                  One table per workspace. A generated query can never read
                  another workspace&apos;s rows — no shared tables, no leaks.
                </p>
              </article>

              <article className="bl-feature">
                <span className="bl-feature__icon">
                  <Code2 size={21} aria-hidden="true" />
                </span>
                <h3>Show the query</h3>
                <p>
                  Every answer carries its SQL plus a raw data preview. Open it,
                  read it, and audit any number in one click.
                </p>
              </article>

              <article className="bl-feature">
                <span className="bl-feature__icon">
                  <Flag size={21} aria-hidden="true" />
                </span>
                <h3>Honest by default</h3>
                <p>
                  Confidence meters, clarifications instead of guesses, and a
                  one-tap flag for shaky answers. Nothing hidden, nothing lost.
                </p>
              </article>
            </div>
          </div>
        </section>

        {/* ---------- TRUST / TRACE ---------- */}
        <section id="trust" className="bl-band bl-trace bl-reveal">
          <div className="bl-band__inner bl-trace__grid">
            <div className="bl-trace__copy">
              <p className="bl-pill">
                <ShieldCheck size={14} aria-hidden="true" /> Trust
              </p>
              <h2>Not a black box. A real query per answer.</h2>
              <p className="bl-band__sub">
                Every answer ships with the exact SQL and raw data behind it, so
                you can check the work — not just the conclusion.
              </p>

              <ul className="bl-checklist">
                {[
                  'Answers tagged as fact or possible factor',
                  'Confidence shown only when it means something',
                  'Flag any answer into the review log',
                ].map((item) => (
                  <li key={item}>
                    <span className="bl-check">
                      <Check size={13} aria-hidden="true" />
                    </span>
                    {item}
                  </li>
                ))}
              </ul>

              <a href="#how" className="bl-textlink">
                See how questions are answered
                <ArrowRight size={14} aria-hidden="true" />
              </a>
            </div>

            <div className="bl-trace__art" aria-hidden="true">
              <div className="bl-code-card">
                <div className="bl-code-card__bar">
                  <span className="bl-app__dots">
                    <i />
                    <i />
                    <i />
                  </span>
                  <span>answer_report.sql</span>
                  <span className="bl-code-card__badge">
                    <ShieldCheck size={11} /> Traced
                  </span>
                </div>
                <pre>
                  <code>
                    <span className="bl-tok-key">SELECT</span> region,{' '}
                    <span className="bl-tok-fn">SUM</span>(revenue){'\n'}
                    <span className="bl-tok-key">FROM</span> workspace_42.sales{'\n'}
                    <span className="bl-tok-key">WHERE</span> week {'>='}{' '}
                    <span className="bl-tok-str">&apos;2026-10-12&apos;</span>
                    {'\n'}
                    <span className="bl-tok-key">GROUP BY</span> region{'\n'}
                    <span className="bl-tok-key">ORDER BY</span> revenue{' '}
                    <span className="bl-tok-key">DESC</span>;
                  </code>
                </pre>
                <div className="bl-code-card__foot">
                  <span>12 rows</span>
                  <span>0.8s</span>
                  <span>only your workspace</span>
                </div>
              </div>

              <div className="bl-confidence-card">
                <p>Confidence</p>
                <strong>70%</strong>
                <div className="bl-meter">
                  <i style={{ width: '70%' }} />
                </div>
                <span>Bounded 0–1, shown only when it means something.</span>
              </div>

              <div className="bl-rows-card">
                <ShieldCheck size={13} />
                12 rows returned
              </div>
            </div>
          </div>
        </section>

        {/* ---------- EXAMPLE ANSWERS ---------- */}
        <section className="bl-band bl-answers bl-reveal">
          <div className="bl-band__inner">
            <div className="bl-band__head">
              <p className="bl-pill">
                <MessageSquare size={14} aria-hidden="true" /> Real questions
              </p>
              <h2>Four questions. Four traced answers.</h2>
              <p className="bl-band__sub">
                Every answer arrives with its chart, its reasoning, and the
                query behind it. Pick one to see the shape.
              </p>
            </div>

            <div className="bl-tabs" role="tablist" aria-label="Example answers">
              {DEMO_EXAMPLES.map((example, index) => (
                <button
                  key={example.id}
                  type="button"
                  role="tab"
                  id={`bl-demo-tab-${example.id}`}
                  aria-selected={index === demoTab}
                  aria-controls="bl-demo-panel"
                  className={`bl-tab${index === demoTab ? ' is-active' : ''}`}
                  onClick={() => setDemoTab(index)}
                >
                  {example.tab}
                </button>
              ))}
            </div>

            <div
              className="bl-answer"
              role="tabpanel"
              id="bl-demo-panel"
              aria-labelledby={`bl-demo-tab-${demo.id}`}
            >
              <p className="bl-answer__q">{demo.question}</p>
              <p className="bl-answer__a">{demo.answer}</p>

              {demo.id === 'revenue' && (
                <>
                  <div className="bl-answer__row">
                    <div className="bl-stat">
                      <strong>−7.4%</strong>
                      <span>WoW revenue</span>
                    </div>
                    <div className="bl-bars" aria-hidden="true">
                      {[
                        { label: 'W1', height: 64, tip: 'W1 · $55.8k' },
                        { label: 'W2', height: 78, tip: 'W2 · $52.4k' },
                        { label: 'W3', height: 70, tip: 'W3 · $59.1k' },
                        { label: 'W4', height: 52, tip: 'W4 · $44.7k' },
                        { label: 'W5', height: 44, tip: 'W5 · $48.9k' },
                      ].map(({ label, height, tip }, index) => (
                        <span key={label} className="bl-bar" data-tip={tip}>
                          <i
                            className={index >= 3 ? 'is-down' : ''}
                            style={{ height: `${height}%` }}
                          />
                          <em>{label}</em>
                        </span>
                      ))}
                    </div>
                  </div>

                  <div className="bl-table__wrap">
                    <table className="bl-table">
                      <thead>
                        <tr>
                          <th>Region</th>
                          <th>Revenue</th>
                          <th>WoW</th>
                        </tr>
                      </thead>
                      <tbody>
                        <tr>
                          <td>West</td>
                          <td>$44.7k</td>
                          <td className="is-down">−12.1%</td>
                        </tr>
                        <tr>
                          <td>East</td>
                          <td>$51.3k</td>
                          <td className="is-down">−2.8%</td>
                        </tr>
                        <tr>
                          <td>North</td>
                          <td>$28.9k</td>
                          <td className="is-up">+1.6%</td>
                        </tr>
                      </tbody>
                    </table>
                  </div>
                </>
              )}

              {demo.id === 'compare' && (
                <>
                  <div className="bl-versus">
                    <div className="bl-vs-card">
                      <span>East</span>
                      <strong>$51.3k</strong>
                      <em className="is-up">+8.2% QoQ</em>
                    </div>
                    <div className="bl-vs-card">
                      <span>West</span>
                      <strong>$44.7k</strong>
                      <em className="is-down">−3.1% QoQ</em>
                    </div>
                  </div>
                  <ul className="bl-share" aria-label="Revenue share by region">
                    {[
                      { region: 'East', share: '34%', revenue: '$51.3k' },
                      { region: 'West', share: '30%', revenue: '$44.7k' },
                      { region: 'North', share: '19%', revenue: '$28.9k' },
                      { region: 'South', share: '17%', revenue: '$25.1k' },
                    ].map(({ region, share, revenue }) => (
                      <li
                        key={region}
                        className="bl-share__row"
                        data-tip={`${region} · ${revenue} · ${share} of total`}
                      >
                        <span className="bl-share__name">{region}</span>
                        <span className="bl-share__track" aria-hidden="true">
                          <i style={{ width: share }} />
                        </span>
                        <span className="bl-share__pct">{share}</span>
                      </li>
                    ))}
                  </ul>
                </>
              )}

              {demo.id === 'forecast' && (
                <>
                  <div className="bl-chart-card">
                    <svg
                      className="bl-spark"
                      viewBox="0 0 600 190"
                      focusable="false"
                    >
                      <g className="bl-spark__grid">
                        <line x1="24" y1="46" x2="576" y2="46" />
                        <line x1="24" y1="88" x2="576" y2="88" />
                        <line x1="24" y1="130" x2="576" y2="130" />
                      </g>
                      <polyline
                        className="bl-spark__actual"
                        points="32,138 148,122 264,96"
                      />
                      <polyline
                        className="bl-spark__projected"
                        points="264,96 380,78 496,52 568,32"
                      />
                      {[
                        { cx: 32, cy: 138, tip: 'Aug · $51.3k · actual' },
                        { cx: 148, cy: 122, tip: 'Sep · $54.0k · actual' },
                        { cx: 264, cy: 96, tip: 'Oct · $58.2k · projected' },
                        { cx: 380, cy: 78, tip: 'Nov · $61.4k · projected' },
                        { cx: 496, cy: 52, tip: 'Dec · $63.8k · projected' },
                        { cx: 568, cy: 32, tip: 'Jan · $66.0k · projected' },
                      ].map(({ cx, cy, tip }) => (
                        <g key={tip} className="bl-spark__point">
                          <title>{tip}</title>
                          <circle cx={cx} cy={cy} r="12" fill="transparent" />
                          <circle cx={cx} cy={cy} r="4.5" />
                        </g>
                      ))}
                      {[
                        { x: 32, label: 'Aug' },
                        { x: 148, label: 'Sep' },
                        { x: 264, label: 'Oct' },
                        { x: 380, label: 'Nov' },
                        { x: 496, label: 'Dec' },
                        { x: 568, label: 'Jan' },
                      ].map(({ x, label }) => (
                        <text
                          key={label}
                          className="bl-spark__axis"
                          x={x}
                          y="166"
                          textAnchor="middle"
                        >
                          {label}
                        </text>
                      ))}
                    </svg>
                    <div className="bl-spark__legend">
                      <span>
                        <i className="is-actual" /> Actual
                      </span>
                      <span>
                        <i className="is-projected" /> Projected
                      </span>
                    </div>
                  </div>
                  <div className="bl-factors">
                    <p>Possible factors</p>
                    <ul>
                      <li>West returning to its 4-week average</li>
                      <li>Two paused wholesale accounts resuming</li>
                    </ul>
                  </div>
                </>
              )}

              {demo.id === 'channels' && (
                <>
                  <div className="bl-answer__row bl-answer__row--channels">
                    <svg
                      className="bl-donut"
                      viewBox="0 0 120 120"
                      role="img"
                      aria-label="Donut chart: Online 46%, Retail 32%, Wholesale 22%"
                    >
                      <circle
                        cx="60"
                        cy="60"
                        r="42"
                        fill="none"
                        className="bl-donut__track"
                      />
                      <g transform="rotate(-90 60 60)">
                        <g className="bl-donut__seg bl-donut__seg--online">
                          <title>Online · 46% · $69.0k</title>
                          <circle
                            cx="60"
                            cy="60"
                            r="42"
                            fill="none"
                            strokeDasharray="121.4 263.9"
                            strokeDashoffset="0"
                          />
                        </g>
                        <g className="bl-donut__seg bl-donut__seg--retail">
                          <title>Retail · 32% · $48.0k</title>
                          <circle
                            cx="60"
                            cy="60"
                            r="42"
                            fill="none"
                            strokeDasharray="84.5 263.9"
                            strokeDashoffset="-121.4"
                          />
                        </g>
                        <g className="bl-donut__seg bl-donut__seg--wholesale">
                          <title>Wholesale · 22% · $33.0k</title>
                          <circle
                            cx="60"
                            cy="60"
                            r="42"
                            fill="none"
                            strokeDasharray="58 263.9"
                            strokeDashoffset="-205.9"
                          />
                        </g>
                      </g>
                      <text className="bl-donut__total" x="60" y="58">
                        $150k
                      </text>
                      <text className="bl-donut__sub" x="60" y="74">
                        Q3 sales
                      </text>
                    </svg>
                    <ul className="bl-legend" aria-label="Sales by channel">
                      {[
                        { channel: 'Online', detail: '46% · $69.0k', mod: 'online' },
                        { channel: 'Retail', detail: '32% · $48.0k', mod: 'retail' },
                        { channel: 'Wholesale', detail: '22% · $33.0k', mod: 'wholesale' },
                      ].map(({ channel, detail, mod }) => (
                        <li key={channel}>
                          <i className={`bl-legend__dot bl-legend__dot--${mod}`} />
                          <span>{channel}</span>
                          <em>{detail}</em>
                        </li>
                      ))}
                    </ul>
                  </div>
                </>
              )}

              <div className="bl-answer__trust">
                <span>
                  <Code2 size={12} aria-hidden="true" /> Show the query
                </span>
                <span>
                  <ShieldCheck size={12} aria-hidden="true" /> {demo.confidence}
                </span>
                <span>
                  <Flag size={12} aria-hidden="true" /> Flag this answer
                </span>
              </div>
            </div>
          </div>
        </section>

        {/* ---------- CTA ---------- */}
        <section className="bl-cta bl-reveal" aria-labelledby="bl-cta-title">
          <div className="bl-cta__aurora" aria-hidden="true">
            <i />
            <i />
          </div>

          <div className="bl-cta__float bl-cta__float--left" aria-hidden="true">
            <p className="bl-float__kicker">Revenue · last week</p>
            <strong>−7.4%</strong>
            <span className="bl-float__meta">West region · 2 paused accounts</span>
            <div className="bl-cta__spark">
              {[54, 70, 62, 44, 36, 48].map((height) => (
                <i key={height} style={{ height: `${height}%` }} />
              ))}
            </div>
          </div>

          <div className="bl-cta__float bl-cta__float--right" aria-hidden="true">
            <p className="bl-float__kicker">
              <Code2 size={13} /> Query traced
            </p>
            <pre>{`SELECT SUM(revenue)\nFROM sales\nWHERE week = 'last';`}</pre>
            <span className="bl-float__foot">
              <ShieldCheck size={12} /> Only your rows
            </span>
          </div>

          <div className="bl-cta__mascot" aria-hidden="true">
            <img src="/logo.png" alt="" />
            <span>Ask anything</span>
          </div>

          <div className="bl-cta__inner">
            <p className="bl-pill bl-pill--accent">
              <Sparkles size={14} aria-hidden="true" /> Start free
            </p>
            <h2 id="bl-cta-title">
              Your data. Your questions.
              <br />
              <span>Your answers.</span>
            </h2>
            <p className="bl-cta__sub">
              Upload your first CSV and ask your first question. You&apos;ll have
              a traced answer in about a minute.
            </p>
            <div className="bl-cta__actions">
              <Link to="/signup" className="bl-btn bl-btn--solid bl-btn--lg">
                Start free <ArrowRight size={16} aria-hidden="true" />
              </Link>
              <Link to="/app" className="bl-btn bl-btn--outline bl-btn--lg">
                Open the app
              </Link>
            </div>
            <p className="bl-cta__note">
              4 free questions · No card · Your data stays yours
            </p>
          </div>
        </section>
      </main>

      {/* ---------- FOOTER ---------- */}
      <footer className="bl-footer">
        {/* Shared mascot scene (public/login.png) — the same art as the auth
            screen, shown whole at its natural ratio so nothing is cropped. */}
        <figure className="bl-footer__scene">
          <img
            src="/login.png"
            alt="The BuildifyLabs fox working on a laptop beside books labelled Graph, Sales, and Statics"
            width={1536}
            height={1024}
            loading="lazy"
          />
        </figure>

        <div className="bl-footer__grid">
          <div>
            <p className="bl-footer__brand">
              <span className="bl-brand__mark" aria-hidden="true">
                <img src="/logo.png" alt="" />
              </span>
              BuildifyLabs
            </p>
            <p className="bl-footer__tag">
              The AI business intelligence copilot for people who&apos;d rather
              ask than build dashboards.
            </p>
            <p className="bl-footer__backed">
              Free to start · 4 questions per window
            </p>
          </div>

          <nav aria-label="Product">
            <p className="bl-footer__head">Product</p>
            <a href="#product">Why BuildifyLabs</a>
            <a href="#how">How it works</a>
            <a href="#trust">Trust</a>
            <Link to="/app">Open the app</Link>
          </nav>

          <div>
            <p className="bl-footer__head">Trust &amp; security</p>
            <ul className="bl-footer__trust">
              <li>
                <ShieldCheck size={15} aria-hidden="true" /> Scoped queries only
              </li>
              <li>
                <Code2 size={15} aria-hidden="true" /> SQL on every answer
              </li>
              <li>
                <Search size={15} aria-hidden="true" /> Export or delete anytime
              </li>
            </ul>
          </div>

          <nav aria-label="Account">
            <p className="bl-footer__head">Get started</p>
            <Link to="/signup">Start free</Link>
            <Link to="/signin">Log in</Link>
            <a href="#how">See a sample answer</a>
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
