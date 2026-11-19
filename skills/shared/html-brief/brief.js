/* Paper Assistant — HTML brief kit (behaviour).
   Inlined into every brief by build_brief.py. Everything here is progressive
   enhancement: the brief must stay fully readable with JavaScript disabled. */
(() => {
  'use strict';
  const doc = document;
  const root = doc.documentElement;
  const $ = (sel, el = doc) => el.querySelector(sel);
  const $$ = (sel, el = doc) => [...el.querySelectorAll(sel)];
  const paperKey = 'brief:' + (root.dataset.paper || location.pathname);
  const store = {
    get(key) { try { return localStorage.getItem(paperKey + ':' + key); } catch (e) { return null; } },
    set(key, value) { try { localStorage.setItem(paperKey + ':' + key, value); } catch (e) { /* private mode */ } },
  };
  root.classList.add('js');

  /* Theme: follow the system unless the reader picks one. */
  const themeButton = $('#theme-toggle');
  const applyTheme = (theme) => {
    if (theme) root.dataset.theme = theme; else delete root.dataset.theme;
    if (themeButton) {
      const dark = theme ? theme === 'dark' : matchMedia('(prefers-color-scheme: dark)').matches;
      themeButton.textContent = dark ? 'Light' : 'Dark';
    }
  };
  let savedTheme = null;
  try { savedTheme = localStorage.getItem('brief:theme'); } catch (e) { /* ignore */ }
  applyTheme(savedTheme);
  if (themeButton) themeButton.addEventListener('click', () => {
    const dark = root.dataset.theme ? root.dataset.theme === 'dark' : matchMedia('(prefers-color-scheme: dark)').matches;
    const next = dark ? 'light' : 'dark';
    try { localStorage.setItem('brief:theme', next); } catch (e) { /* ignore */ }
    applyTheme(next);
  });

  /* Section rail: current section, visited ticks, overall progress. */
  const navLinks = $$('.rail nav a[href^="#"]');
  const sections = navLinks.map((a) => doc.getElementById(a.hash.slice(1))).filter(Boolean);
  const bar = $('.rail .progress i');
  const onScroll = () => {
    const line = window.scrollY + window.innerHeight * 0.3;
    let current = 0;
    sections.forEach((section, i) => { if (section.offsetTop <= line) current = i; });
    navLinks.forEach((a, i) => {
      if (i === current) a.setAttribute('aria-current', 'true'); else a.removeAttribute('aria-current');
      if (i < current) a.classList.add('done');
    });
    if (bar) {
      const max = doc.documentElement.scrollHeight - window.innerHeight;
      bar.style.width = (max > 0 ? Math.min(100, (window.scrollY / max) * 100) : 100) + '%';
    }
  };
  if (sections.length) {
    let ticking = false;
    window.addEventListener('scroll', () => {
      if (ticking) return;
      ticking = true;
      requestAnimationFrame(() => { onScroll(); ticking = false; });
    }, { passive: true });
    onScroll();
  }

  /* Glossary popovers. Without JS a term is a plain link to its glossary entry. */
  let popover = null;
  let popoverFor = null;
  const hidePopover = () => { if (popover) popover.remove(); popover = null; popoverFor = null; };
  const showPopover = (term) => {
    const entry = doc.getElementById(term.hash.slice(1));
    if (!entry) return;
    const definition = entry.nextElementSibling;
    hidePopover();
    popover = doc.createElement('div');
    popover.className = 'popover';
    popover.setAttribute('role', 'tooltip');
    const title = doc.createElement('b');
    title.textContent = entry.textContent;
    popover.append(title);
    if (definition) popover.append(...[...definition.cloneNode(true).childNodes]);
    doc.body.append(popover);
    const rect = term.getBoundingClientRect();
    const width = popover.offsetWidth;
    const left = Math.max(8, Math.min(rect.left + window.scrollX, window.scrollX + doc.documentElement.clientWidth - width - 8));
    let top = rect.bottom + window.scrollY + 6;
    if (rect.bottom + popover.offsetHeight + 12 > window.innerHeight) top = rect.top + window.scrollY - popover.offsetHeight - 6;
    popover.style.left = left + 'px';
    popover.style.top = top + 'px';
    popoverFor = term;
  };
  $$('a.term').forEach((term) => {
    term.addEventListener('mouseenter', () => showPopover(term));
    term.addEventListener('mouseleave', hidePopover);
    term.addEventListener('focus', () => showPopover(term));
    term.addEventListener('blur', hidePopover);
    term.addEventListener('click', (event) => {
      /* First tap shows the definition; a second tap follows the link. */
      if (popoverFor !== term) { event.preventDefault(); showPopover(term); }
    });
  });
  doc.addEventListener('keydown', (event) => { if (event.key === 'Escape') hidePopover(); });
  window.addEventListener('scroll', hidePopover, { passive: true });

  /* Paper figures open full size in a dialog. */
  const zoomable = $$('figure.paper-fig img');
  if (zoomable.length && typeof HTMLDialogElement !== 'undefined') {
    const dialog = doc.createElement('dialog');
    dialog.className = 'zoom';
    const big = doc.createElement('img');
    dialog.append(big);
    doc.body.append(dialog);
    dialog.addEventListener('click', () => dialog.close());
    zoomable.forEach((img) => {
      img.tabIndex = 0;
      img.setAttribute('role', 'button');
      img.setAttribute('aria-label', (img.alt || 'Figure') + ' — open full size');
      const open = () => { big.src = img.src; big.alt = img.alt; dialog.showModal(); };
      img.addEventListener('click', open);
      img.addEventListener('keydown', (event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); open(); } });
    });
  }

  /* Check yourself: reveal, self-rate, and point back to what to re-read. */
  const checks = $$('details.check');
  const summary = $('#check-summary');
  const updateCheckSummary = () => {
    if (!summary || !checks.length) return;
    const got = checks.filter((c) => c.dataset.state === 'got').length;
    const again = checks.filter((c) => c.dataset.state === 'again');
    summary.replaceChildren();
    if (!got && !again.length) { summary.textContent = 'Answer in your own words before opening each one.'; return; }
    summary.append(`${got} of ${checks.length} solid.`);
    if (again.length) {
      summary.append(' Re-read: ');
      const seen = new Set();
      again.forEach((c) => {
        const target = c.dataset.review;
        if (!target || seen.has(target)) return;
        const heading = $(target + ' h2, ' + target + ' h3') || $(target);
        if (!heading) return;
        if (seen.size) summary.append(', ');
        seen.add(target);
        const a = doc.createElement('a');
        a.href = target;
        a.textContent = heading.textContent.trim().replace(/[.:?!]+$/, '');
        summary.append(a);
      });
      summary.append('.');
    }
  };
  checks.forEach((check, index) => {
    const key = 'check:' + (check.id || index);
    const rate = doc.createElement('div');
    rate.className = 'rate';
    rate.append('Before reading on: ');
    const buttons = [['got', 'I had it'], ['again', 'Not yet']].map(([value, label]) => {
      const button = doc.createElement('button');
      button.type = 'button';
      button.className = 'chip';
      button.dataset.rate = value;
      button.textContent = label;
      button.setAttribute('aria-pressed', 'false');
      button.addEventListener('click', () => setState(check.dataset.state === value ? '' : value));
      rate.append(button);
      return button;
    });
    const setState = (value) => {
      if (value) check.dataset.state = value; else delete check.dataset.state;
      buttons.forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.rate === value)));
      store.set(key, value || '');
      updateCheckSummary();
    };
    const answer = $('.answer', check);
    (answer || check).append(rate);
    const saved = store.get(key);
    if (saved) setState(saved);
  });
  updateCheckSummary();

  /* Expand or collapse every disclosure (check-yourself answers stay closed). */
  const expandButton = $('#expand-toggle');
  if (expandButton) expandButton.addEventListener('click', () => {
    const targets = $$('details:not(.check)');
    const open = targets.some((d) => !d.open);
    targets.forEach((d) => { d.open = open; });
    expandButton.textContent = open ? 'Collapse details' : 'Expand details';
  });

  /* Print everything, then restore what was open. */
  const printButton = $('#print-button');
  if (printButton) printButton.addEventListener('click', () => window.print());
  let openBeforePrint = null;
  window.addEventListener('beforeprint', () => {
    if (openBeforePrint) return;
    const all = $$('details');
    openBeforePrint = all.map((d) => d.open);
    all.forEach((d) => { d.open = true; });
  });
  window.addEventListener('afterprint', () => {
    if (!openBeforePrint) return;
    $$('details').forEach((d, i) => { d.open = openBeforePrint[i]; });
    openBeforePrint = null;
  });
})();
