const API_URL = "https://20fphbhsbc.execute-api.us-east-1.amazonaws.com/prod/task";

document.getElementById("infoForm").addEventListener("submit", async (e) => {
  e.preventDefault();

  const payload = {
    task: document.getElementById("task").value.trim(),
    description: document.getElementById("description").value.trim(),
    // datetime-local returns "YYYY-MM-DDTHH:mm"
    dueDate: document.getElementById("dueDate").value,
    target: document.getElementById("target").value.trim(), // e.g. "U12345,U67890" or "@name,@name2"
  };
  document.getElementById("status").textContent = "Submitting...";

  try {
    const res = await fetch(API_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    const data = await res.json();
    if (!res.ok) throw new Error(data?.message || "Request failed");

    document.getElementById("status").textContent = `Submitted! Task ID: ${data.taskId}`;
  } catch (err) {
    document.getElementById("status").textContent = `Error: ${err.message}`;
  }
});
