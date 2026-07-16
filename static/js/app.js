(() => {
  const csrf = document.querySelector('meta[name="csrf-token"]')?.content || '';
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  let formDirty = false;   // becomes true once there is unsaved work on the page
  let saveAttempts = 0;    // blank-field save attempts (popup on the 2nd)
  let voucherBlocked = false; // advance-voucher: selected vehicle already has an open trip

  // The vehicle whose trip flow is currently in progress (carried across steps this session).
  const ACTIVE_KEY = 'crm_active_vehicle';
  const getActiveVehicle = () => { try { return sessionStorage.getItem(ACTIVE_KEY) || ''; } catch (_) { return ''; } };
  const setActiveVehicle = v => { try { v ? sessionStorage.setItem(ACTIVE_KEY, v) : sessionStorage.removeItem(ACTIVE_KEY); } catch (_) {} };

  function toast(message, type = 'success') {
    let stack = $('.toast-stack');
    if (!stack) {
      stack = document.createElement('div');
      stack.className = 'toast-stack';
      document.body.appendChild(stack);
    }
    const el = document.createElement('div');
    el.className = `toast ${type}`;
    el.textContent = message;
    stack.appendChild(el);
    setTimeout(() => el.remove(), 4200);
  }

  async function api(url, opts = {}) {
    const headers = opts.headers || {};
    if (!(opts.body instanceof FormData)) headers['Content-Type'] = 'application/json';
    if (csrf) headers['X-CSRF-Token'] = csrf;
    const res = await fetch(url, { credentials: 'same-origin', ...opts, headers });
    const isJson = (res.headers.get('content-type') || '').includes('application/json');
    const data = isJson ? await res.json() : await res.text();
    if (!res.ok || (isJson && data.ok === false)) {
      const err = new Error((data && data.message) || `Request failed (${res.status})`);
      err.data = isJson ? data : null;
      throw err;
    }
    return data;
  }

  // Lightweight promise-based confirm popup (no template needed).
  function confirmDialog(message, okLabel = 'Continue', title = 'Please confirm') {
    return new Promise(resolve => {
      const modal = document.createElement('div');
      modal.className = 'modal';
      modal.innerHTML = `
        <div class="modal-card small-modal">
          <div class="modal-head"><h3>${title}</h3><button class="ghost" type="button" data-x>×</button></div>
          <p style="padding:18px 22px;line-height:1.5">${message}</p>
          <div class="modal-actions" style="padding:0 22px 18px">
            <button class="btn secondary" type="button" data-cancel>Cancel</button>
            <button class="btn primary" type="button" data-ok>${okLabel}</button>
          </div>
        </div>`;
      document.body.appendChild(modal);
      const done = val => { modal.remove(); resolve(val); };
      modal.addEventListener('click', e => { if (e.target === modal) done(false); });
      $('[data-x]', modal).addEventListener('click', () => done(false));
      $('[data-cancel]', modal).addEventListener('click', () => done(false));
      $('[data-ok]', modal).addEventListener('click', () => done(true));
    });
  }

  function serializeForm(form) {
    const data = {};
    const fd = new FormData(form);
    for (const [key, value] of fd.entries()) data[key] = value;
    $$('input[type="checkbox"]', form).forEach(ch => { data[ch.name] = ch.checked ? '1' : ''; });
    $$('select:disabled,input[readonly],textarea[readonly]', form).forEach(el => {
      if (el.name) data[el.name] = el.value;
    });
    const rows = [];
    $$('#fuelLineTable tbody tr').forEach(tr => {
      const row = {};
      $$('input', tr).forEach(inp => row[inp.name] = inp.value);
      if (Object.values(row).some(Boolean)) rows.push(row);
    });
    if (rows.length) data.fuel_lines = rows;
    return data;
  }

  function fillForm(form, data) {
    Object.entries(data || {}).forEach(([key, val]) => {
      const els = $$(`[name="${CSS.escape(key)}"]`, form);
      els.forEach(el => {
        if (el.type === 'radio') el.checked = el.value === String(val);
        else if (el.type === 'checkbox') el.checked = Boolean(val) && val !== '0';
        else if (el.tagName === 'SELECT') {
          const sval = val == null ? '' : String(val);
          // If the saved value isn't one of the options (e.g. a custom FTL type), add it so it shows selected.
          if (sval && !Array.from(el.options).some(o => o.value === sval)) el.add(new Option(sval, sval));
          el.value = sval;
        } else el.value = val ?? '';
      });
    });
  }

  function draftKey() {
    return window.CRM_STEP ? `logistics_crm_draft_${window.CRM_STEP}` : '';
  }

  function saveDraft() {
    const form = $('#operationForm');
    if (!form || !draftKey()) return;
    localStorage.setItem(draftKey(), JSON.stringify(serializeForm(form)));
  }

  function loadDraft() {
    const form = $('#operationForm');
    if (!form || !draftKey()) return;
    try {
      const raw = localStorage.getItem(draftKey());
      if (raw) fillForm(form, JSON.parse(raw));
    } catch (_) {}
  }

  // ----- Theme (light / dark) toggle, persisted -----
  function applyThemeIcon() {
    const btn = $('#themeToggle');
    if (!btn) return;
    const theme = document.documentElement.getAttribute('data-theme') || 'dark';
    btn.textContent = theme === 'dark' ? '🌙' : '☀️';
    btn.title = theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode';
  }
  applyThemeIcon();
  const themeToggle = $('#themeToggle');
  if (themeToggle) themeToggle.addEventListener('click', () => {
    const current = document.documentElement.getAttribute('data-theme') || 'dark';
    const next = current === 'dark' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', next);
    try { localStorage.setItem('crm_theme', next); } catch (_) {}
    applyThemeIcon();
  });

  const sidebarToggle = $('#sidebarToggle');
  if (sidebarToggle) sidebarToggle.addEventListener('click', () => {
    const sidebar = $('#sidebar');
    const shell = $('.shell');
    if (window.innerWidth <= 860) sidebar.classList.toggle('show');
    else { sidebar.classList.toggle('collapsed'); shell.classList.toggle('full'); }
  });

  const sidebarClose = $('#sidebarClose');
  if (sidebarClose) sidebarClose.addEventListener('click', () => {
    const sidebar = $('#sidebar');
    const shell = $('.shell');
    if (window.innerWidth <= 860) sidebar.classList.remove('show');
    else { sidebar.classList.add('collapsed'); shell.classList.add('full'); }
  });

  $$('.close-modal').forEach(btn => btn.addEventListener('click', () => btn.closest('.modal')?.classList.add('hidden')));
  $$('.modal').forEach(modal => modal.addEventListener('click', e => { if (e.target === modal) modal.classList.add('hidden'); }));

  const divisionSwitcher = $('#divisionSwitcher');
  function currentDivisionName() {
    if (!divisionSwitcher) return '';
    return divisionSwitcher.options[divisionSwitcher.selectedIndex]?.textContent.trim() || '';
  }
  function divisionNameList() {
    return divisionSwitcher ? $$('option', divisionSwitcher).map(o => o.textContent.trim()) : [];
  }
  // Vendor name follows the current division (and changes when division is switched).
  function syncVendorToDivision(name, force) {
    name = name || currentDivisionName();
    const vsel = $('[name="vendor_name"]');
    if (vsel) {
      const keep = vsel.value;                       // preserve current pick across rebuild
      const want = (force || !keep) ? name : keep;
      if (vsel.tagName === 'SELECT') {
        const names = divisionNameList();
        if (names.length) vsel.innerHTML = '<option value="">--Select--</option>' + names.map(n => `<option value="${n}">${n}</option>`).join('');
        if (want && !Array.from(vsel.options).some(o => o.value === want)) vsel.add(new Option(want, want));
      }
      vsel.value = want;
    }
    const dname = $('[name="division_name"]');
    if (dname && divisionSwitcher && (force || !dname.value)) {
      const id = String(divisionSwitcher.value).padStart(4, '0');
      dname.value = `DIV${id}:${name}`;
    }
  }
  if (divisionSwitcher) {
    divisionSwitcher.addEventListener('change', async () => {
      saveDraft();
      try {
        const data = await api('/api/division/change', { method: 'POST', body: JSON.stringify({ division_id: divisionSwitcher.value }) });
        syncVendorToDivision(data.division.name, true);
        saveDraft();
        toast(`Division changed to ${data.division.name}. Vendor updated.`, 'success');
      } catch (err) {
        toast(err.message, 'error');
      }
    });
  }

  function openVehicleModal(prefill = {}) {
    const modal = $('#vehicleModal');
    const form = $('#vehicleForm');
    if (!modal || !form) return;
    form.reset();
    fillForm(form, prefill);
    if (divisionSwitcher && !prefill.current_division_id) form.current_division_id.value = divisionSwitcher.value;
    modal.classList.remove('hidden');
  }

  $$('[data-open-vehicle-modal]').forEach(btn => btn.addEventListener('click', () => {
    const form = $('#operationForm');
    const data = form ? serializeForm(form) : {};
    openVehicleModal({
      vehicle_no: data.vehicle_no || '',
      ftl_type: data.ftl_type || '',
      driver_name: data.driver_name || '',
      driver_mobile: data.driver_mobile || '',
      fuel_type: data.fuel_type || '',
      tank_capacity: data.tank_capacity || '',
      mileage: data.mileage || '',
      last_closing_km: data.last_closing_km || '',
      opening_km: data.opening_km || '',
    });
  }));

  let pendingSwitchDivision = null;
  async function checkVehicle(vehicleNo, sourceInput) {
    const clean = (vehicleNo || '').replace(/[^a-zA-Z0-9]/g, '').toUpperCase();
    const hint = $('#vehicleHint');
    if (!clean || clean.length < 4) return;
    if (sourceInput) sourceInput.value = clean;
    try {
      const data = await api(`/api/vehicle/${encodeURIComponent(clean)}`);
      if (!data.found) {
        voucherBlocked = false;
        if (hint) { hint.textContent = 'Vehicle not found. Use Add Vehicle popup.'; hint.className = 'hint vehicle-hint warn'; }
        return;
      }
      const form = $('#operationForm');
      // Pull master record + everything captured on the open trip so far (cross-step / cross-day autofill).
      if (form && data.autofill) fillForm(form, data.autofill);
      recalc();
      setActiveVehicle(clean);
      voucherBlocked = false;
      if (hint) {
        hint.textContent = `Vehicle found in ${data.division?.name || 'N/A'} division. Details auto-filled.`;
        hint.className = 'hint vehicle-hint ok';
      }
      // One active trip per vehicle.
      if (window.CRM_STEP === 'advance-voucher' && data.has_open_trip) {
        voucherBlocked = true;
        if (hint) {
          hint.textContent = `${clean} already active trip (${data.open_trip_no}) me hai — naya voucher tab banega jab Trip Expense complete ho.`;
          hint.className = 'hint vehicle-hint warn';
        }
        toast(`${clean} ka ek trip already chal raha hai (${data.open_trip_no}). Pehle uska Trip Expense complete karein.`, 'warn');
      } else if (window.CRM_STEP !== 'advance-voucher' && data.has_open_trip && hint) {
        hint.textContent = `Trip ${data.open_trip_no} continue ho rahi hai. Pichhle steps ka data auto-filled.`;
        hint.className = 'hint vehicle-hint ok';
      }
      if (data.needs_division_switch && data.division) {
        pendingSwitchDivision = data.division;
        $('#divisionMismatchText').textContent = `${clean} vehicle ${data.division.name} division me hai. Bina logout/refresh ke division switch karke continue karein. Bhara hua data safe rahega.`;
        $('#confirmDivisionModal').classList.remove('hidden');
      }
      formDirty = true;
      saveDraft();
    } catch (err) {
      if (hint) { hint.textContent = 'Vehicle lookup failed. Try again.'; hint.className = 'hint vehicle-hint warn'; }
    }
  }

  // Type-ahead dropdown of matching vehicle numbers from the database.
  function attachVehicleAutocomplete(inp) {
    const wrap = inp.closest('.vehicle-field') || inp.parentElement;
    wrap.classList.add('ac-wrap');
    const dd = document.createElement('div');
    dd.className = 'autocomplete-list hidden';
    wrap.appendChild(dd);
    const close = () => dd.classList.add('hidden');
    let acTimer = null;
    inp.addEventListener('input', () => {
      const q = inp.value.replace(/[^a-zA-Z0-9]/g, '').toUpperCase();
      inp.value = q;
      formDirty = true;
      clearTimeout(acTimer);
      if (q.length < 1) { close(); return; }
      acTimer = setTimeout(async () => {
        try {
          const data = await api(`/api/vehicle/suggest?q=${encodeURIComponent(q)}`);
          if (!data.results.length) { close(); return; }
          dd.innerHTML = data.results.map(r =>
            `<button type="button" data-vno="${r.vehicle_no}"><strong>${r.vehicle_no}</strong><span>${[r.division_name, r.driver_name].filter(Boolean).join(' · ')}</span></button>`
          ).join('');
          dd.classList.remove('hidden');
          $$('button', dd).forEach(b => b.addEventListener('mousedown', e => {
            e.preventDefault();
            inp.value = b.dataset.vno; close(); checkVehicle(inp.value, inp);
          }));
        } catch (_) { close(); }
      }, 200);
    });
    inp.addEventListener('blur', () => { setTimeout(close, 150); checkVehicle(inp.value, inp); });
    inp.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); close(); checkVehicle(inp.value, inp); } });
    document.addEventListener('click', e => { if (!wrap.contains(e.target)) close(); });
  }
  $$('[data-vehicle-input]').forEach(attachVehicleAutocomplete);

  const switchVehicleDivision = $('#switchVehicleDivision');
  if (switchVehicleDivision) {
    switchVehicleDivision.addEventListener('click', async () => {
      if (!pendingSwitchDivision) return;
      saveDraft();
      try {
        const data = await api('/api/division/change', { method: 'POST', body: JSON.stringify({ division_id: pendingSwitchDivision.id }) });
        if (divisionSwitcher) divisionSwitcher.value = data.division.id;
        syncVendorToDivision(data.division.name, true);
        saveDraft();
        $('#confirmDivisionModal').classList.add('hidden');
        toast(`Division switched to ${data.division.name}. Vendor updated, data preserved.`, 'success');
      } catch (err) { toast(err.message, 'error'); }
    });
  }

  // Driver mobile: digits only, hard cap at 10 (can't type more).
  $$('[name="driver_mobile"]').forEach(inp => {
    inp.addEventListener('input', () => { inp.value = inp.value.replace(/\D/g, '').slice(0, 10); });
  });

  const vehicleForm = $('#vehicleForm');
  if (vehicleForm) vehicleForm.addEventListener('submit', async e => {
    e.preventDefault();
    const mob = $('[name="driver_mobile"]', vehicleForm)?.value || '';
    if (mob && mob.length !== 10) { toast('Driver mobile must be exactly 10 digits.', 'error'); return; }
    try {
      const data = await api('/api/vehicle', { method: 'POST', body: JSON.stringify(serializeForm(vehicleForm)) });
      toast(data.message, 'success');
      $('#vehicleModal').classList.add('hidden');
      const form = $('#operationForm');
      if (form && data.vehicle) fillForm(form, {
        vehicle_no: data.vehicle.vehicle_no,
        ftl_type: data.vehicle.ftl_type || '',
        driver_name: data.vehicle.driver_name || '',
        driver_mobile: data.vehicle.driver_mobile || '',
        fuel_type: data.vehicle.fuel_type || '',
        tank_capacity: data.vehicle.tank_capacity || '',
        mileage: data.vehicle.mileage || '',
        last_closing_km: data.vehicle.last_closing_km || '',
        opening_km: data.vehicle.opening_km || '',
        vehicle_type: data.vehicle.vehicle_type || '',
      });
      recalc();
      formDirty = true;
      saveDraft();
    } catch (err) { toast(err.message, 'error'); }
  });

  // ---- Master data selects: payment accounts (filtered by Cash/Bank/Fuel) and pumps ----
  async function populateAccounts(preserve) {
    const sel = $('[name="payment_account"]');
    if (!sel) return;
    const mode = ($('[name="amount_paid_by"]:checked')?.value) || 'CASH';
    try {
      const data = await api(`/api/payment-accounts?type=${encodeURIComponent(mode)}`);
      const cur = preserve != null ? preserve : sel.value;
      sel.innerHTML = '<option value="">--Select--</option>' + data.results.map(r => `<option value="${r.name}">${r.name}</option>`).join('');
      if (cur && Array.from(sel.options).some(o => o.value === cur)) sel.value = cur;
    } catch (_) {}
  }
  async function populatePumps(preserve) {
    const sel = $('[name="pump_name"]');
    if (!sel) return;
    try {
      const data = await api('/api/pumps');
      const cur = preserve != null ? preserve : sel.value;
      sel.innerHTML = '<option value="">--Select Fuel Provider--</option>' + data.results.map(r => `<option value="${r.name}">${r.name}</option>`).join('');
      if (cur && Array.from(sel.options).some(o => o.value === cur)) sel.value = cur;
    } catch (_) {}
  }

  // Put a small "+ Add" button beside a select (wraps select + button in a flex row).
  function injectAddButton(selectName, label, handler) {
    const sel = $(`[name="${selectName}"]`);
    if (!sel || sel.dataset.addWired) return;
    sel.dataset.addWired = '1';
    const row = document.createElement('div');
    row.className = 'inline-add-row';
    sel.parentNode.insertBefore(row, sel);
    row.appendChild(sel);
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'btn mini secondary';
    btn.textContent = label;
    btn.addEventListener('click', handler);
    row.appendChild(btn);
  }

  if (window.CRM_STEP === 'advance-voucher') {
    injectAddButton('payment_account', '+ Account', () => {
      const modal = $('#accountModal');
      const mode = ($('[name="amount_paid_by"]:checked')?.value) || 'CASH';
      if ($('#accountForm [name="acc_type"]')) $('#accountForm [name="acc_type"]').value = mode;
      modal?.classList.remove('hidden');
    });
    populateAccounts($('[name="payment_account"]')?.value);
    $$('[name="amount_paid_by"]').forEach(r => r.addEventListener('change', () => populateAccounts('')));
  }
  if (window.CRM_STEP === 'advance-fuel') {
    injectAddButton('pump_name', '+ Pump', () => $('#pumpModal')?.classList.remove('hidden'));
    populatePumps($('[name="pump_name"]')?.value);
  }

  const pumpForm = $('#pumpForm');
  if (pumpForm) pumpForm.addEventListener('submit', async e => {
    e.preventDefault();
    try {
      const data = await api('/api/pumps', { method: 'POST', body: JSON.stringify(serializeForm(pumpForm)) });
      $('#pumpModal').classList.add('hidden');
      pumpForm.reset();
      await populatePumps(data.name);
      toast(data.message, 'success');
    } catch (err) { toast(err.message, 'error'); }
  });

  const accountForm = $('#accountForm');
  if (accountForm) accountForm.addEventListener('submit', async e => {
    e.preventDefault();
    try {
      const payload = serializeForm(accountForm);
      const data = await api('/api/payment-accounts', { method: 'POST', body: JSON.stringify(payload) });
      $('#accountModal').classList.add('hidden');
      // Switch the payment mode to match the new account's type, then select it.
      const radio = $(`[name="amount_paid_by"][value="${payload.acc_type}"]`);
      if (radio) { radio.checked = true; }
      accountForm.reset();
      await populateAccounts(data.name);
      recalc();
      toast(data.message, 'success');
    } catch (err) { toast(err.message, 'error'); }
  });

  const bulkVehicleForm = $('#bulkVehicleForm');
  if (bulkVehicleForm) bulkVehicleForm.addEventListener('submit', async e => {
    e.preventDefault();
    const result = $('#bulkResult');
    result.textContent = 'Uploading...';
    try {
      const fd = new FormData(bulkVehicleForm);
      const data = await api('/api/vehicle/bulk', { method: 'POST', body: fd, headers: { 'X-CSRF-Token': csrf } });
      result.textContent = `Added: ${data.added}, Updated: ${data.updated}${data.errors?.length ? ', Errors: ' + data.errors.join(' | ') : ''}`;
      toast('Bulk upload completed.', 'success');
    } catch (err) {
      result.textContent = err.message;
      toast(err.message, 'error');
    }
  });

  function addFuelLine(row = {}) {
    const tbody = $('#fuelLineTable tbody');
    if (!tbody) return;
    const tr = document.createElement('tr');
    const cols = [
      ['refuel', 'text'], ['qty', 'number'], ['rate', 'number'], ['date_time', 'datetime-local'], ['final_price', 'number'], ['remark', 'text']
    ];
    tr.innerHTML = cols.map(([name, type]) => `<td><input name="${name}" type="${type}" value="${row[name] || ''}"></td>`).join('') + '<td><button class="btn mini danger" type="button">×</button></td>';
    $('button', tr).addEventListener('click', () => { tr.remove(); saveDraft(); });
    $$('input', tr).forEach(inp => inp.addEventListener('input', saveDraft));
    tbody.appendChild(tr);
  }
  $$('[data-add-line]').forEach(btn => btn.addEventListener('click', () => addFuelLine()));
  if ($('#fuelLineTable') && !$('#fuelLineTable tbody tr')) addFuelLine();

  function n(v) { const x = parseFloat(v || '0'); return Number.isFinite(x) ? x : 0; }
  function setVal(name, value) { const el = $(`[name="${name}"]`); if (el) el.value = Number(value || 0).toFixed(2); }

  function recalc() {
    const form = $('#operationForm');
    if (!form) return;
    const d = serializeForm(form);
    if (window.CRM_STEP === 'advance-voucher') {
      const payable = n(d.payable_advance), paid = n(d.paid_advance);
      setVal('balance_advance', payable - paid);
      setVal('total_advance', paid);
      const payMode = d.amount_paid_by || 'CASH';
      const hint = $('#paymentHint');
      if (hint) hint.textContent = payMode === 'BANK' ? 'Bank account and Paid To A/C No required.' : payMode === 'FUEL' ? 'Fuel provider / pump account selected.' : 'Cash account selected.';
      const account = $('[name="payment_account"]');
      if (account) {
        if (payMode === 'BANK') account.title = 'Select bank account';
        if (payMode === 'FUEL') account.title = 'Select fuel provider';
      }
    }
    if (window.CRM_STEP === 'advance-fuel') {
      const amount = n(d.fuel_rate) * n(d.required_fuel_qty);
      setVal('fuel_amount', amount);
      setVal('required_advance', amount - n(d.driver_adjustment));
      setVal('req_fuel_amount', amount);
      setVal('balanced_fuel_qty', n(d.req_fuel_qty) - n(d.required_fuel_qty));
    }
    if (window.CRM_STEP === 'trip-creation') {
      const basic = n(d.rate) * Math.max(n(d.weight), n(d.packet), 1);
      const extra = n(d.loading_unloading)+n(d.green_tax)+n(d.halting_charge)+n(d.touching_charge)+n(d.holding_charge)+n(d.detention_damage_charge)+n(d.labour_charge);
      const deduction = n(d.deduction_charge);
      const taxable = Math.max(0, basic + extra - deduction);
      setVal('basic_freight', basic);
      setVal('total_deduction', deduction);
      setVal('taxable_amt', taxable);
      setVal('cgst_tax', taxable * n(d.cgst_percent) / 100);
      setVal('sgst_tax', taxable * n(d.sgst_percent) / 100);
      setVal('igst_tax', taxable * n(d.igst_percent) / 100);
      setVal('total_freight', taxable + n($('[name="cgst_tax"]')?.value) + n($('[name="sgst_tax"]')?.value) + n($('[name="igst_tax"]')?.value));
    }
    if (window.CRM_STEP === 'trip-performance') {
      const start = d.tp_start_date ? new Date(d.tp_start_date) : null;
      const end = d.tp_closed_date ? new Date(d.tp_closed_date) : null;
      if (start && end && end >= start) setVal('no_of_days', Math.ceil((end - start) / 86400000) + 1);
    }
    if (window.CRM_STEP === 'trip-expense') {
      const expenseKeys = ['extra_diesel','toll_tax_exp','naka','bhati','rto','entry','challan','labour_charges','accidental_settlement','cash_toll','incentive','police_expense','other','repair_maintenance','urea_charge','pod_charge','parking_charge','fooding','atm_charge','diesel_cash_exp','pesgi','phone_charge','air_grease','monthly_mechanical_exp','advance_salary','weight_exp_slip'];
      const driverExpense = expenseKeys.reduce((sum, key) => sum + n(d[key]), 0) + (n(d.fuel_qty) * n(d.fuel_rate));
      const freightAdv = n(d.branch_advance)+n(d.advance)+n(d.other_advance)+n(d.diesel_cash)+n(d.total_fuel)+n(d.toll_tax)+n(d.loading_unloading_amt)-n(d.deduct_adv_amt);
      setVal('total_freight_advance', freightAdv);
      setVal('total_freight_advance_final', freightAdv);
      setVal('driver_expense', driverExpense);
      setVal('total_tp_expense', driverExpense + n(d.route_survey_charge)+n(d.driver_incentive)+n(d.agent_commission)+n(d.behanthi)+n(d.loading_charge)+n(d.unloading_charge)+n(d.halting_charge)-n(d.penalty_deduction));
      const payable = Math.max(0, n($('[name="total_tp_expense"]')?.value) - freightAdv);
      const receivable = Math.max(0, freightAdv - n($('[name="total_tp_expense"]')?.value));
      setVal('payable_to_driver', payable);
      setVal('receivable_from_driver', receivable);
      setVal('balance', freightAdv - driverExpense);
    }
  }

  // Auto-fill current date/time into any empty date/time field (still manually editable).
  function autofillDateTime(form) {
    const now = new Date();
    const pad = n => String(n).padStart(2, '0');
    const dateStr = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
    const timeStr = `${pad(now.getHours())}:${pad(now.getMinutes())}`;
    const dtStr = `${dateStr}T${timeStr}`;
    $$('input[type="date"]', form).forEach(el => { if (!el.value && !el.readOnly) el.value = dateStr; });
    $$('input[type="time"]', form).forEach(el => { if (!el.value && !el.readOnly) el.value = timeStr; });
    $$('input[type="datetime-local"]', form).forEach(el => { if (!el.value && !el.readOnly) el.value = dtStr; });
  }

  // Refresh the auto-generated TP No. when the associated company changes (advance-voucher).
  let tpTimer = null;
  function refreshTpNo(form) {
    const companyEl = $('[name="assoc_company"]', form);
    const tpEl = $('[name="tp_no"]', form);
    if (!companyEl || !tpEl) return;
    clearTimeout(tpTimer);
    tpTimer = setTimeout(async () => {
      try {
        const data = await api(`/api/operations/next-tp-no?company=${encodeURIComponent(companyEl.value || '')}`);
        if (data.tp_no) { tpEl.value = data.tp_no; saveDraft(); }
      } catch (_) {}
    }, 350);
  }

  // Fields that must be filled before a step can save (matches the production form).
  // 2nd save attempt offers an override popup, as requested.
  const REQUIRED = {
    'advance-voucher': ['vehicle_no', 'voucher_date', 'voucher_time', 'vendor_name', 'ftl_type',
      'last_closing_km', 'opening_km', 'route', 'fuel_type', 'advance_type', 'amount_paid_by',
      'payment_account', 'payable_advance', 'paid_advance'],
    'advance-fuel': ['vehicle_no', 'tp_no', 'fuel_filling_qty', 'pump_name', 'fuel_rate',
      'required_fuel_qty', 'fuel_slip_no'],
    'trip-creation': ['vehicle_no', 'loading_date', 'loading_time', 'reporting_date', 'route',
      'route_distance', 'entry_type', 'load_type', 'rate_type', 'on_account', 'grn_challan_no',
      'grn_challan_date', 'consignor', 'consignee', 'rate', 'weight', 'packet'],
    'trip-reporting': ['vehicle_no', 'reporting_date', 'reporting_time', 'party_name', 'route', 'route_code'],
    'trip-unloading': ['vehicle_no', 'unloading_date', 'unloading_time', 'party_name', 'route', 'route_code'],
    'trip-performance': ['vehicle_no', 'tp_start_date', 'tp_closed_date', 'perf_date', 'opening_km', 'closing_km'],
    'trip-expense': ['vehicle_no', 'tp_close_date', 'tp_exp_create_date', 'select_driver'],
  };
  // Put a red * on the labels of required fields for the current step.
  function markRequiredLabels(form) {
    (REQUIRED[window.CRM_STEP] || []).forEach(name => {
      const el = $(`[name="${CSS.escape(name)}"]`, form);
      const lab = el?.closest('.field') ? $('label', el.closest('.field')) : null;
      if (lab && !$('.req', lab)) {
        const star = document.createElement('em');
        star.className = 'req';
        star.textContent = ' *';
        lab.appendChild(star);
      }
    });
  }
  function labelFor(form, name) {
    const el = $(`[name="${CSS.escape(name)}"]`, form);
    const lab = el?.closest('.field') ? $('label', el.closest('.field')) : null;
    return lab ? lab.textContent.trim() : name;
  }
  function missingRequired(form) {
    const d = serializeForm(form);
    return (REQUIRED[window.CRM_STEP] || []).filter(k => !String(d[k] ?? '').trim());
  }
  function handleSaveError(err) {
    const d = err.data || {};
    if (d.code === 'division_mismatch' && d.division) {
      pendingSwitchDivision = d.division;
      $('#divisionMismatchText').textContent = `${d.message}`;
      $('#confirmDivisionModal').classList.remove('hidden');
      return;
    }
    toast(err.message, d.code === 'trip_active' ? 'warn' : 'error');
  }

  const opForm = $('#operationForm');
  if (opForm) {
    markRequiredLabels(opForm);
    loadDraft();
    autofillDateTime(opForm);
    if (localStorage.getItem(draftKey())) formDirty = true; // unsaved work restored -> warn on reload
    // Cross-step carry: continue the same vehicle on the next steps without re-typing.
    const vInit = $('[name="vehicle_no"]', opForm);
    if (vInit && !vInit.value && window.CRM_STEP !== 'advance-voucher') {
      const av = getActiveVehicle();
      if (av) { vInit.value = av; checkVehicle(av, vInit); }
    }
    const assocCompany = $('[name="assoc_company"]', opForm);
    if (assocCompany) assocCompany.addEventListener('input', () => refreshTpNo(opForm));
    syncVendorToDivision();   // populate vendor from divisions, default to current
    recalc();
    opForm.addEventListener('input', () => { formDirty = true; recalc(); saveDraft(); });
    opForm.addEventListener('change', () => { formDirty = true; recalc(); saveDraft(); });
    opForm.addEventListener('submit', async e => {
      e.preventDefault();
      recalc();
      if (voucherBlocked) {
        toast('Is vehicle ka ek trip already active hai — naya Advance Voucher nahi ban sakta.', 'error');
        return;
      }
      const missing = missingRequired(opForm);
      if (missing.length) {
        saveAttempts += 1;
        const names = missing.map(k => labelFor(opForm, k)).join(', ');
        if (saveAttempts < 2) {
          toast(`Ye zaroori fields bharein: ${names}`, 'warn');
          $(`[name="${CSS.escape(missing[0])}"]`, opForm)?.focus();
          return;
        }
        const ok = await confirmDialog(`Ye fields abhi blank hain: <b>${names}</b>.<br>Blank ke saath continue karein?`, 'Haan, save karo', 'Kuch fields blank hain');
        if (!ok) return;
      }
      const btn = $('#saveNextBtn');
      btn.disabled = true;
      try {
        const data = await api('/api/operations/save-step', { method: 'POST', body: JSON.stringify({ step: window.CRM_STEP, data: serializeForm(opForm) }) });
        toast(data.message, 'success');
        saveAttempts = 0;
        formDirty = false;
        localStorage.removeItem(draftKey());
        // Carry this vehicle to the next steps; clear once the trip is closed (trip-expense).
        const vno = serializeForm(opForm).vehicle_no || '';
        if (window.CRM_STEP === 'trip-expense') setActiveVehicle('');
        else if (vno) setActiveVehicle(vno);
        if (data.next_step) {
          setTimeout(() => { window.location.href = `/operations/${data.next_step}`; }, 650);
        } else {
          toast('Operations process completed.', 'success');
        }
      } catch (err) { handleSaveError(err); }
      finally { btn.disabled = false; }
    });
  }

  // Warn before reload/close if there is unsaved work on the page.
  window.addEventListener('beforeunload', e => {
    if (formDirty) { e.preventDefault(); e.returnValue = ''; }
  });

  const backBtn = $('#backBtn');
  if (backBtn) backBtn.addEventListener('click', () => {
    saveDraft();
    formDirty = false; // draft is saved; don't double-prompt on navigation
    if (window.CRM_PREV) window.location.href = `/operations/${window.CRM_PREV}`;
    else history.back();
  });

  const printBtn = $('#printBtn');
  if (printBtn) printBtn.addEventListener('click', async () => {
    const form = $('#operationForm');
    if (!form) return;
    recalc();
    try {
      const data = await api('/api/pdf/create', { method: 'POST', body: JSON.stringify({ step: window.CRM_STEP, data: serializeForm(form) }) });
      window.open(data.view_url, '_blank', 'noopener,noreferrer');
    } catch (err) { toast(err.message, 'error'); }
  });

  const savePdfBtn = $('#savePdfBtn');
  if (savePdfBtn) savePdfBtn.addEventListener('click', async () => {
    try {
      const data = await api(`/api/pdf/save/${savePdfBtn.dataset.pdfId}`, { method: 'POST', body: JSON.stringify({}) });
      toast(data.message, 'success');
    } catch (err) { toast(err.message, 'error'); }
  });

  // ---- Pending-trip reminders (bell + post-login popup) ----
  async function loadReminders(openModal) {
    const body = $('#reminderBody');
    const badge = $('#bellCount');
    try {
      const data = await api('/api/reminders');
      if (badge) { badge.textContent = data.count; badge.classList.toggle('hidden', !data.count); }
      if (body) {
        if (!data.count) {
          body.innerHTML = '<p class="hint" style="padding:22px">Koi pending trip nahi — sab clear hai ✅</p>';
        } else {
          body.innerHTML = '<div class="reminder-list">' + data.results.map(r => `
            <div class="reminder-item">
              <div class="reminder-top"><strong>${r.vehicle_no}</strong><span class="badge">${r.tp_no}</span></div>
              <div class="reminder-sub">${r.division_name} • Done: ${r.done.length}/7</div>
              <div class="reminder-steps">Pending: ${r.pending_titles.join(', ') || '—'}</div>
              ${r.next_step ? `<button class="btn mini primary" data-continue data-vehicle="${r.vehicle_no}" data-step="${r.next_step}">Continue → ${r.pending_titles[0]}</button>` : ''}
            </div>`).join('') + '</div>';
          $$('#reminderBody [data-continue]').forEach(b => b.addEventListener('click', () => {
            setActiveVehicle(b.dataset.vehicle);
            window.location.href = `/operations/${b.dataset.step}`;
          }));
        }
      }
      if (openModal && data.count) $('#reminderModal')?.classList.remove('hidden');
    } catch (_) {}
  }
  const reminderBell = $('#reminderBell');
  if (reminderBell) {
    reminderBell.addEventListener('click', () => { $('#reminderModal')?.classList.remove('hidden'); loadReminders(false); });
    loadReminders(window.CRM_SHOW_REMINDERS === true);
  }
})();
