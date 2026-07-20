(() => {
  'use strict';
  if (typeof Chart === 'undefined') return;
  const $ = (id) => document.getElementById(id);

  // Theme-aware colours (re-read on theme toggle).
  const css = (v, fb) => (getComputedStyle(document.documentElement).getPropertyValue(v) || fb).trim();
  function palette() {
    return {
      ink: css('--ink', '#eef1f4'),
      muted: css('--muted', '#7e8893'),
      line: css('--line', '#1e242c'),
      brand: css('--brand', '#36c2a8'),
      brand2: css('--brand-2', '#2a9d8f'),
      good: css('--good', '#3ccf9b'),
      warn: css('--warn', '#e6b450'),
      danger: css('--danger', '#e86a76'),
    };
  }
  const money = (v) => '₹' + Math.round(Number(v || 0)).toLocaleString('en-IN');
  const charts = {};

  function baseOpts(p, legend = true) {
    return {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: legend, labels: { color: p.muted, boxWidth: 12, font: { size: 11 } } },
        tooltip: { backgroundColor: '#0b0f14', titleColor: '#fff', bodyColor: '#dfe6ec', borderColor: p.line, borderWidth: 1 },
      },
      scales: undefined,
    };
  }
  function axisOpts(p) {
    return {
      x: { ticks: { color: p.muted, font: { size: 11 } }, grid: { color: p.line } },
      y: { ticks: { color: p.muted, font: { size: 11 } }, grid: { color: p.line }, beginAtZero: true },
    };
  }

  function render(data) {
    const p = palette();
    Object.values(charts).forEach((c) => c && c.destroy());

    const t = data.totals || {};
    $('tFreight').textContent = money(t.freight);
    $('tExpense').textContent = money(t.expense);
    $('tDriver').textContent = money(t.driver_expense);
    const profitEl = $('tProfit');
    profitEl.textContent = money(t.profit);
    profitEl.style.color = Number(t.profit) < 0 ? p.danger : p.good;

    // 1. Profit / Loss by month
    const m = data.monthly || [];
    charts.pnl = new Chart($('chartPnl'), {
      type: 'bar',
      data: {
        labels: m.map((x) => x.month),
        datasets: [
          { label: 'Freight', data: m.map((x) => x.freight), backgroundColor: p.brand, borderRadius: 6 },
          { label: 'Expense', data: m.map((x) => x.expense), backgroundColor: p.warn, borderRadius: 6 },
          { label: 'Profit', type: 'line', data: m.map((x) => x.profit), borderColor: p.good, backgroundColor: p.good, tension: .35, borderWidth: 2, pointRadius: 3 },
        ],
      },
      options: { ...baseOpts(p), scales: axisOpts(p) },
    });

    // 2. Freight vs Expense per vehicle
    const v = data.vehicles || [];
    charts.veh = new Chart($('chartVehicles'), {
      type: 'bar',
      data: {
        labels: v.map((x) => x.vehicle_no),
        datasets: [
          { label: 'Freight', data: v.map((x) => x.freight), backgroundColor: p.brand, borderRadius: 6 },
          { label: 'Expense', data: v.map((x) => x.expense), backgroundColor: p.danger, borderRadius: 6 },
        ],
      },
      options: { ...baseOpts(p), indexAxis: 'y', scales: axisOpts(p) },
    });

    // 3. Vehicles by division
    const d = data.divisions || [];
    charts.div = new Chart($('chartDivisions'), {
      type: 'doughnut',
      data: {
        labels: d.map((x) => x.name),
        datasets: [{ data: d.map((x) => x.count), backgroundColor: [p.brand, p.brand2, p.good, p.warn, p.danger, '#7d8ea1'], borderWidth: 0 }],
      },
      options: { ...baseOpts(p), cutout: '62%' },
    });

    // 4. Trip status
    const s = data.status || [];
    charts.status = new Chart($('chartStatus'), {
      type: 'doughnut',
      data: {
        labels: s.map((x) => x.status),
        datasets: [{ data: s.map((x) => x.count), backgroundColor: [p.good, p.warn, p.brand, p.danger], borderWidth: 0 }],
      },
      options: { ...baseOpts(p), cutout: '62%' },
    });

    // 5. Accounts by type
    const a = data.accounts || [];
    charts.acc = new Chart($('chartAccounts'), {
      type: 'bar',
      data: {
        labels: a.map((x) => x.acc_type),
        datasets: [{ label: 'Accounts', data: a.map((x) => x.count), backgroundColor: p.brand2, borderRadius: 6 }],
      },
      options: { ...baseOpts(p, false), scales: axisOpts(p) },
    });

    // 6. Overall money split
    charts.overall = new Chart($('chartOverall'), {
      type: 'polarArea',
      data: {
        labels: ['Freight', 'Expense', 'Peshgi', 'Driver Exp.'],
        datasets: [{ data: [t.freight || 0, t.expense || 0, t.peshgi || 0, t.driver_expense || 0], backgroundColor: [p.brand, p.warn, p.brand2, p.danger], borderWidth: 0 }],
      },
      options: { ...baseOpts(p), scales: { r: { ticks: { display: false }, grid: { color: p.line } } } },
    });
  }

  let cached = null;
  async function load() {
    try {
      const res = await fetch('/api/dashboard/stats', { credentials: 'same-origin' });
      cached = await res.json();
      if (cached.ok) render(cached);
    } catch (_) {}
  }
  load();

  // Redraw with new colours when the theme is toggled.
  const themeBtn = document.getElementById('themeToggle');
  if (themeBtn) themeBtn.addEventListener('click', () => setTimeout(() => { if (cached && cached.ok) render(cached); }, 60));
})();
