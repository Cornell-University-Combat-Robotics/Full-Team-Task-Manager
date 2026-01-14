document.getElementById("infoForm").addEventListener("submit", (e) => {
  e.preventDefault();

  const name = document.getElementById("name").value;
  const email = document.getElementById("email").value;

  document.getElementById("status").textContent =
    `Thanks, ${name}! Your info was captured locally.`;

  // Later: send this to a backend or form service
  console.log({ name, email });
});
