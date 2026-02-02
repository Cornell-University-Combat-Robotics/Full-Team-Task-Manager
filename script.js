const API_URL = "https://20fphbhsbc.execute-api.us-east-1.amazonaws.com/prod/task";

// ---------- Custom reminder UI wiring ----------
const form = document.getElementById("infoForm");
const remindSelect = document.getElementById("remind");
const customBox = document.getElementById("customRemindDiv");
const addReminderBtn = document.getElementById("addReminder");
const reminderList = document.getElementById("reminderList");
const remindersJson = document.getElementById("remindersJson");
const statusEl = document.getElementById("status");

// ---------- Target selection UI wiring ----------
const targetSelect = document.getElementById("targetSelect");
const targetCustom = document.getElementById("targetCustom");
const targetHint = document.getElementById("targetHint");

let reminders = []; // [{ amount: 10, unit: "minutes" }, ...]

function syncHiddenField() {
  if (remindersJson) remindersJson.value = JSON.stringify(reminders);
}

function renderReminderRow(reminder, index) {
  const row = document.createElement("div");
  row.style.display = "flex";
  row.style.alignItems = "center";
  row.style.gap = "8px";

  const amountInput = document.createElement("input");
  amountInput.type = "number";
  amountInput.min = "0";
  amountInput.step = "1";
  amountInput.value = String(reminder.amount ?? 10);
  amountInput.style.width = "90px";

  const unitSelect = document.createElement("select");
  ["minutes", "hours", "days", "weeks"].forEach((u) => {
    const opt = document.createElement("option");
    opt.value = u;
    opt.textContent = u;
    if (u === reminder.unit) opt.selected = true;
    unitSelect.appendChild(opt);
  });

  const beforeText = document.createElement("span");
  beforeText.textContent = "before";

  const removeBtn = document.createElement("button");
  removeBtn.type = "button";
  removeBtn.textContent = "✕";
  removeBtn.title = "Remove notification";

  amountInput.addEventListener("input", () => {
    reminders[index].amount = Number(amountInput.value);
    syncHiddenField();
  });

  unitSelect.addEventListener("change", () => {
    reminders[index].unit = unitSelect.value;
    syncHiddenField();
  });

  removeBtn.addEventListener("click", () => {
    reminders.splice(index, 1);
    renderReminderList();
  });

  row.appendChild(amountInput);
  row.appendChild(unitSelect);
  row.appendChild(beforeText);
  row.appendChild(removeBtn);

  return row;
}

function renderReminderList() {
  if (!reminderList) return;
  reminderList.innerHTML = "";
  reminders.forEach((r, i) => reminderList.appendChild(renderReminderRow(r, i)));
  syncHiddenField();
}

function showOrHideCustomUI() {
  const isCustom = remindSelect.value === "custom";
  if (customBox) customBox.style.display = isCustom ? "block" : "none";

  // If switching to custom and empty, add a default like Google Calendar
  if (isCustom && reminders.length === 0) {
    reminders.push({ amount: 10, unit: "minutes" });
    renderReminderList();
  }

  // If switching away from custom, keep reminders in memory (so user can toggle back)
  // If you prefer clearing them, uncomment:
  // if (!isCustom) { reminders = []; renderReminderList(); }
}

if (remindSelect) remindSelect.addEventListener("change", showOrHideCustomUI);

if (addReminderBtn) {
  addReminderBtn.addEventListener("click", () => {
    reminders.push({ amount: 10, unit: "minutes" });
    renderReminderList();
  });
}

// ---------- Target selection toggle ----------
function showOrHideTargetCustom() {
  const isOther = targetSelect.value === "other";
  if (targetCustom) {
    targetCustom.style.display = isOther ? "block" : "none";
    if (isOther) {
      targetCustom.required = true;
    } else {
      targetCustom.required = false;
    }
  }
  if (targetHint) {
    targetHint.style.display = isOther ? "block" : "none";
  }
}

if (targetSelect) targetSelect.addEventListener("change", showOrHideTargetCustom);

// Initialize UI on page load
showOrHideCustomUI();
renderReminderList();
showOrHideTargetCustom();

// ---------- Form submit (your original API call, extended) ----------
form.addEventListener("submit", async (e) => {
  e.preventDefault();

  const remindValue = remindSelect.value;

  // Build payload
  // Determine target value based on dropdown selection
  let targetValue;
  if (targetSelect.value === "other") {
    targetValue = targetCustom.value.trim();
  } else {
    targetValue = targetSelect.value;
  }

  const payload = {
    task: document.getElementById("task").value.trim(),
    description: document.getElementById("description").value.trim(),
    dueDate: document.getElementById("dueDate").value, // "YYYY-MM-DDTHH:mm"
    target: targetValue,
    remindType: remindValue,
  };

  // Only include reminders if custom selected
  if (remindValue === "custom") {
    // sanitize: keep only valid entries
    const cleaned = reminders
      .map((r) => ({
        amount: Number(r.amount),
        unit: String(r.unit || "minutes"),
      }))
      .filter(
        (r) =>
          Number.isFinite(r.amount) &&
          r.amount >= 0 &&
          ["minutes", "hours", "days", "weeks"].includes(r.unit)
      );

    payload.reminders = cleaned;
  }

  statusEl.textContent = "Submitting...";

  try {
    const res = await fetch(API_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data?.message || "Request failed");

    statusEl.textContent = `Submitted! Task ID: ${data.taskId || "(no id returned)"}`;
    form.reset();

    // optional: keep/clear UI state after submit
    // clear reminders + hide custom UI after reset
    reminders = [];
    renderReminderList();
    showOrHideCustomUI();
  } catch (err) {
    statusEl.textContent = `Error: ${err.message}`;
  }
});
