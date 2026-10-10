// Copy buttons on code blocks. Code blocks rendered from Markdown get one too.
for (const pre of document.querySelectorAll(".prose pre")) {
  const box = document.createElement("div");
  box.className = "code";
  pre.replaceWith(box);
  const button = document.createElement("button");
  button.type = "button";
  button.className = "copy";
  button.setAttribute("aria-label", "Copy code");
  button.textContent = "Copy";
  box.append(button, pre);
}

for (const button of document.querySelectorAll(".code .copy")) {
  button.addEventListener("click", async () => {
    const text = button.parentElement.querySelector("pre").innerText.replace(/\n$/, "");
    try {
      await navigator.clipboard.writeText(text);
      button.textContent = "Copied";
    } catch {
      button.textContent = "Select and copy";
    }
    setTimeout(() => (button.textContent = "Copy"), 1600);
  });
}
