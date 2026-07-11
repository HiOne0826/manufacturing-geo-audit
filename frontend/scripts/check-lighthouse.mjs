import fs from "node:fs";

const path = "test-results/lighthouse-accessibility.json";
const report = JSON.parse(fs.readFileSync(path, "utf8"));
const score = Math.round((report.categories?.accessibility?.score || 0) * 100);
if (score < 95) {
  console.error(`Lighthouse Accessibility ${score}，低于 95`);
  process.exit(1);
}
console.log(`Lighthouse Accessibility ${score}`);
