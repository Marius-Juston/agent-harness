// (b) reference matcher: the node-ignore module embedded in Claude Code, extracted by b_extract_ignore.py.
// usage: node b_ignore_run.js IGNORE.js < cases.json   (cases = [[globs[], relpath], ...]) -> JSON [bool, ...]
// Claude Code 2.1.316 decides a rule's `paths:` with ignore().add(globs).ignores(relpath) (see the note, §2).
const fs = require("fs");
const src = fs.readFileSync(process.argv[2], "utf8").replace(/^module\.exports=/, "");
const factory = (0, eval)(src);
const mod = { exports: {} };
factory({}, mod);
const ignore = mod.exports;
const cases = JSON.parse(fs.readFileSync(0, "utf8"));
// Claude Code's xst() first drops each pattern node-ignore cannot compile (its Kf(): add([p]).test("probe") throws).
const compiles = (g) => {
  try {
    ignore().add([g]).test("probe");
    return true;
  } catch {
    return false;
  }
};
console.log(JSON.stringify(cases.map(([globs, p]) => ignore().add(globs.filter(compiles)).ignores(p))));
