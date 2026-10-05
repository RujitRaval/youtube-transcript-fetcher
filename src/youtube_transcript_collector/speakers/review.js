"use strict";
let analysis,
  edits,
  token,
  saving = false;
const pending = new Map();
const $ = (id) => document.getElementById(id);
const label = (id) => edits.names[id] || `Speaker ${id.slice(8)}`;
const time = (s) => new Date(Math.floor(s) * 1000).toISOString().slice(11, 19);
function status(message, error = false) {
  $("status").textContent = message;
  $("status").classList.toggle("error", error);
}
function node(tag, text, cls) {
  const el = document.createElement(tag);
  if (text !== undefined) el.textContent = text;
  if (cls) el.className = cls;
  return el;
}
async function save(patch) {
  if (saving) return false;
  saving = true;
  document
    .querySelectorAll("button,input,select")
    .forEach((b) => (b.disabled = true));
  status("Saving…");
  try {
    const response = await fetch("/api/edits", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Review-Token": token },
      body: JSON.stringify({
        run_id: analysis.run_id,
        revision: edits.revision,
        ...patch,
      }),
    });
    const data = await response.json();
    if (!response.ok) throw Error(data.error);
    edits = data;
    for (const index of Object.keys(patch.overrides || {}))
      pending.delete(index);
    render();
    status(`Saved to disk · revision ${edits.revision}`);
    return true;
  } catch (error) {
    status(
      `${error.message}. Your unsaved inputs remain here. Reload to resolve a stale review.`,
      true,
    );
    return false;
  } finally {
    saving = false;
    document
      .querySelectorAll("button,input,select")
      .forEach((b) => (b.disabled = false));
  }
}
function renderNames() {
  const container = $("names");
  container.replaceChildren();
  for (const id of Object.keys(edits.names)) {
    const field = node("label", `Speaker ${id.slice(8)}`);
    const input = node("input");
    input.type = "text";
    input.maxLength = 120;
    input.dataset.speaker = id;
    input.value = edits.names[id];
    input.placeholder = "Enter a name";
    input.setAttribute("aria-label", `Speaker ${id.slice(8)} name`);
    field.append(input);
    container.append(field);
  }
}
function render() {
  const container = $("transcript");
  container.replaceChildren();
  let flagged = 0;
  for (const cue of analysis.captions) {
    const index = String(cue.source_cue_index),
      manual = Object.hasOwn(edits.overrides, index),
      review = !manual && cue.review_reasons.length > 0;
    if (review) flagged++;
    if ($("filter").checked && !review) continue;
    const ids = manual ? edits.overrides[index] : cue.speaker_ids;
    const row = node("div", undefined, "cue"),
      seek = node("button", time(cue.start), "seek");
    seek.setAttribute("aria-label", `Play from ${time(cue.start)}`);
    seek.onclick = () => {
      $("audio").currentTime = cue.start;
      $("audio")
        .play()
        .catch((e) => status(e.message, true));
    };
    const body = node("div");
    body.append(
      node(
        "span",
        (ids.length ? ids : cue.candidate_speakers).map(label).join(" / ") ||
          "Review required",
        "speaker",
      ),
    );
    if (review) body.append(node("span", " · Review", "review"));
    if (manual) body.append(node("span", " · Manually corrected"));
    body.append(node("p", cue.text, "text"));
    const detail = node("details", undefined, "correct");
    detail.append(node("summary", "Correct speaker assignment"));
    const field = node("label", "Select voices (multiple selection allowed)");
    const select = node("select");
    select.multiple = true;
    select.size = Math.min(Object.keys(edits.names).length, 5);
    select.setAttribute("aria-label", `Speakers at ${time(cue.start)}`);
    for (const id of Object.keys(edits.names)) {
      const option = node("option", label(id));
      option.value = id;
      option.selected = (pending.get(index) || ids).includes(id);
      select.append(option);
    }
    select.onchange = () =>
      pending.set(
        index,
        [...select.selectedOptions].map((o) => o.value),
      );
    field.append(select);
    detail.append(field);
    const correct = node("button", "Save caption");
    correct.onclick = () => {
      const selected = [...select.selectedOptions].map((o) => o.value);
      if (!selected.length) {
        status("Select at least one speaker.", true);
        return;
      }
      save({ overrides: { [index]: selected } });
    };
    const reset = node("button", "Use model prediction");
    reset.onclick = () => save({ overrides: { [index]: null } });
    detail.append(correct, reset);
    body.append(detail);
    row.append(seek, body);
    container.append(row);
  }
  $("stats").textContent =
    `${Object.keys(edits.names).length} voice clusters · ${analysis.captions.length} captions · ${flagged} need review${analysis.partial ? " · PARTIAL AUDIO SAMPLE" : ""}. Labels remain unverified until you review them.`;
  $("history").textContent = JSON.stringify(edits.history, null, 2);
}
$("save").onclick = async () => {
  const names = {};
  $("names")
    .querySelectorAll("input")
    .forEach((input) => (names[input.dataset.speaker] = input.value));
  if (await save({ names })) renderNames();
};
$("filter").onchange = render;
(async () => {
  try {
    const response = await fetch("/api/state");
    const data = await response.json();
    if (!response.ok) throw Error(data.error);
    analysis = data.analysis;
    edits = data.edits;
    token = data.token;
    $("title").textContent = analysis.title;
    $("audio").src = "/audio.wav?" + analysis.run_id;
    document
      .querySelectorAll("a[download]")
      .forEach((a) => (a.href += "?" + analysis.run_id));
    renderNames();
    render();
    $("save").disabled = false;
    status(`Loaded saved revision ${edits.revision}`);
  } catch (e) {
    status(e.message, true);
  }
})();
