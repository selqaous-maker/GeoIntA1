const fileInput = document.querySelector("#file");
const analyze = document.querySelector("#analyze");
const preview = document.querySelector("#preview");
const empty = document.querySelector("#empty");
const result = document.querySelector("#result");
const report = document.querySelector("#report");
const country = document.querySelector("#country");
const place = document.querySelector("#place");
const confidence = document.querySelector("#confidence");
const health = document.querySelector("#health");
let currentFile = null;

fetch("/health").then(r => r.json()).then(d => health.textContent = d.ok ? "GeoINT ready" : "GeoINT partial").catch(() => health.textContent = "Backend offline");

fileInput.addEventListener("change", () => {
  currentFile = fileInput.files[0] || null;
  analyze.disabled = !currentFile;
  result.hidden = true;
  if (!currentFile) return;
  preview.src = URL.createObjectURL(currentFile);
  preview.style.display = "block";
  empty.style.display = "none";
});

analyze.addEventListener("click", async () => {
  if (!currentFile) return;
  analyze.disabled = true;
  analyze.textContent = "Analyzing...";
  const form = new FormData();
  form.append("file", currentFile);
  try {
    const response = await fetch("/api/analyze", { method: "POST", body: form });
    const data = await response.json();
    if (!response.ok) throw new Error(JSON.stringify(data.detail || data));
    country.textContent = data.guess.country;
    place.textContent = data.guess.place;
    confidence.textContent = data.guess.confidence;
    report.textContent = data.markdown;
  } catch (error) {
    country.textContent = "Error";
    place.textContent = "Could not analyze";
    confidence.textContent = "-";
    report.textContent = String(error.message || error);
  } finally {
    result.hidden = false;
    analyze.disabled = false;
    analyze.textContent = "Analyze";
  }
});
