const { writeFileSync } = require("fs");
const ver = process.env.RELEASE_VERSION;
if (!ver) {
  console.error("RELEASE_VERSION env is missing");
  process.exit(1);
}
writeFileSync("VERSION", ver.trim() + "\n");
console.log("Wrote VERSION:", ver.trim());
