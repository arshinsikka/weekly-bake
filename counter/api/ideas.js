/* Counter: ideas for a shop. One POST with one context the page builds: the
   shop's own menu, how its prices compare with other cafes, what its chosen
   competitors charge, offer and added lately, and offers elsewhere. Each fact
   in it has a short id. A Gemini model, through OpenRouter, writes 3 ideas the
   shop could try this month and names the ids of the facts behind each; the
   page shows its own sentence for each id, never the model's. With mode
   "one" it writes a single idea instead: a new one in place of one turned
   down, or one the owner asked to change. Nothing is stored. One line is logged for each try, and it never holds the key, a
   header or anything of the menu. */
import { MODELS, askChain, askModel, isBusy, isLimit } from "./_models.js";

// The model asked first; the others in _models.js are asked if it cannot answer.
export const IDEAS_MODEL = {id: "google/gemini-3.8-flash", reasoning: {effort: "medium", exclude: true}};
const CHAIN = [IDEAS_MODEL].concat(MODELS.filter(m => m.id !== IDEAS_MODEL.id));

const MAX_BODY = 40000;          // characters
const MAX_MENU = 150;            // own menu rows
const IDEAS = 3;
const TITLE_MAX = 80, WHAT_MAX = 400, WHY_MAX = 300, ITEMS_MAX = 6, IDS_MAX = 8;
const SHOWN_MAX = 30, REQUEST_MAX = 200;
const KINDS = ["set", "offer", "price_change", "new_item"];     // what an idea is: the page shows "Usually $X" for a set only
const PRICE_MIN = 0.1, PRICE_MAX = 999;
// The platform stops this function at 60 seconds (vercel.json). All the tries
// together are given a little less, so the reply below is sent before that.
// One try is stopped at 45 seconds. A busy or slow model is asked again after
// 3 seconds, and a new try starts only with 15 seconds or more left.
const TIME = {overallMs: 55000, tryMs: 45000, waitMs: 3000, needsMs: 15000};

const SYSTEM = [
  "You are helping the owner of an independent cafe in Singapore decide what to try next with their menu and prices.",
  "You get their menu, how their prices compare with other cafes, and what the cafes around them are doing.",
  "Suggest three ideas they could try this month: a set, an offer, a price change, or a new item.",
  "Take inspiration from their competitors and from offers elsewhere, but make each idea fit this cafe; do not just copy.",
  "When there is little or nothing about competitors, work from the menu itself: what pairs well, gaps in the range, items that could carry a set or an offer, and its price points. Offers from elsewhere can still help.",
  "Be specific (which items, what price) and practical for a small team. Write in plain, friendly English, and keep it short.",
  "Prices are as printed on menus, before service charge and GST, and each belongs to one list: dine-in, takeaway or online. When you mention a current price, use the one given.",
  "For each idea, list the ids of the facts that inspired it. Leave out ideas the owner has already seen in already_suggested.",
  "Where it makes sense, mix the kinds of ideas rather than giving three of the same kind.",
  "Use British spelling.",
  "Sometimes you are asked for one new idea instead of three; make it different from the ideas already shown or turned down.",
  "Sometimes the owner asks you to change one idea. Keep what still fits, change what they asked for, and keep it practical for this cafe."
].join(" ");

const SCHEMA = {
  type: "object",
  properties: {
    ideas: {
      type: "array",
      items: {
        type: "object",
        properties: {
          kind: {type: "string", enum: KINDS},
          title: {type: "string"},
          what_to_do: {type: "string"},
          why: {type: "string"},
          items: {type: "array", items: {type: "string"}},
          suggested_price: {type: ["number", "null"]},
          evidence_ids: {type: "array", items: {type: "string"}}
        },
        required: ["kind", "title", "what_to_do", "why", "items", "suggested_price", "evidence_ids"]
      }
    }
  },
  required: ["ideas"]
};
const SHAPE = "kind (set, offer, price_change or new_item), title (at most 8 words), what_to_do (1 to 2 sentences), why (1 sentence), items (names from own_menu), " +
  "suggested_price (a number, or null) and evidence_ids";
const ASK = `Here is the cafe's data as JSON. Reply with 3 ideas: ${SHAPE}.\n\n`;
/* mode "one": one new idea, or one idea changed as the owner asked, never one already shown or turned down */
function askOneText(context, shown, change){
  const seen = shown.length ? "\n\nIdeas already shown or turned down:\n" + shown.map(i => `- ${i.title}: ${i.what_to_do}`).join("\n") : "";
  const ask = change
    ? `Change this one idea as the owner asks, and reply with it as 1 idea: ${SHAPE}.\nThe idea: ${JSON.stringify(change.idea)}\nWhat the owner asks: ${JSON.stringify(change.request)}`
    : `Reply with 1 new idea: ${SHAPE}.`;
  return `Here is the cafe's data as JSON. ${ask}${seen}\n\n${JSON.stringify(context)}`;
}

const MESSAGES = {
  method: "Send a POST request.",
  not_set_up: "Ideas are not switched on yet.",
  bad_request: "We could not use this request.",
  too_large: "This request is too large.",
  limit: "Too many ideas are being written right now. Try again in a minute.",
  failed: "Something went wrong while writing ideas. Try again.",
  busy: "The AI is busy right now. Wait a minute and try again."
};
function reply(status, body){
  return new Response(JSON.stringify(body), {
    status: status,
    headers: {"Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store"}
  });
}
const fail = (status, error) => reply(status, {error: error, message: MESSAGES[error]});
const text = (v, max) => typeof v === "string" ? v.trim().slice(0, max) : "";
const refOf = f => f.stage === "ai_http" ? `ai_http_${f.status}` : f.stage;

/* the context the page sent: an object with a menu of at least one named row */
function contextOk(c){
  return !!c && typeof c === "object" && !Array.isArray(c) && Array.isArray(c.own_menu) && c.own_menu.length >= 1 &&
    c.own_menu.length <= MAX_MENU && c.own_menu.every(r => r && typeof r.name === "string");
}
/* the ids the context gave, wherever they are */
function idsIn(v, out){
  if(Array.isArray(v)) v.forEach(x => idsIn(x, out));
  else if(v && typeof v === "object") Object.keys(v).forEach(k => { if(k === "id" && typeof v[k] === "string") out.add(v[k]); else idsIn(v[k], out); });
  return out;
}
/* One idea as the model gave it -> an idea the page can trust, or null to drop it. Items are kept
   only when they are on the menu, in the menu's own words; ids only when the context gave them. */
function cleanIdea(i, names, ids){
  if(!i || typeof i !== "object") return null;
  const title = text(i.title, TITLE_MAX), what = text(i.what_to_do, WHAT_MAX);
  if(!title || !what) return null;
  const items = (Array.isArray(i.items) ? i.items : []).map(x => names.get(text(x, 200).toLowerCase())).filter(Boolean);
  const p = i.suggested_price;
  return {
    kind: KINDS.indexOf(i.kind) !== -1 ? i.kind : null,
    title: title,
    what_to_do: what,
    why: text(i.why, WHY_MAX),
    items: [...new Set(items)].slice(0, ITEMS_MAX),
    suggested_price: typeof p === "number" && Number.isFinite(p) && p >= PRICE_MIN && p <= PRICE_MAX ? Math.round(p * 100) / 100 : null,
    evidence_ids: [...new Set((Array.isArray(i.evidence_ids) ? i.evidence_ids : []).filter(x => typeof x === "string" && ids.has(x)))].slice(0, IDS_MAX)
  };
}

/* mode "one": the ideas already shown or turned down, and the idea to change with the owner's request.
   Gives {shown, change} or null when they are not usable. */
function oneOk(body){
  const shown = body.shown === undefined ? [] : body.shown;
  if(!Array.isArray(shown) || shown.length > SHOWN_MAX) return null;
  const list = shown.map(i => i && typeof i === "object" ? {title: text(i.title, TITLE_MAX), what_to_do: text(i.what_to_do, WHAT_MAX)} : null);
  if(list.some(i => !i || !i.title)) return null;
  if(body.change === undefined || body.change === null) return {shown: list, change: null};
  const c = body.change, idea = c && typeof c === "object" ? c.idea : null, request = c ? text(c.request, REQUEST_MAX) : "";
  if(!idea || typeof idea !== "object" || !text(idea.title, TITLE_MAX) || !request) return null;
  return {shown: list, change: {request: request, idea: {title: text(idea.title, TITLE_MAX), what_to_do: text(idea.what_to_do, WHAT_MAX),
    why: text(idea.why, WHY_MAX), items: (Array.isArray(idea.items) ? idea.items : []).map(x => text(x, 200)).filter(Boolean).slice(0, ITEMS_MAX),
    suggested_price: typeof idea.suggested_price === "number" && Number.isFinite(idea.suggested_price) ? idea.suggested_price : null}}};
}

/* One ask of one model, and the reply checked for the ideas' shape. one: {shown, change} for mode "one". */
async function askOne(model, context, key, allowed, fullTry, one){
  const words = one ? askOneText(context, one.shown, one.change) : ASK + JSON.stringify(context);
  const messages = [{role: "system", content: SYSTEM}, {role: "user", content: [{type: "text", text: words}]}];
  // the system message goes in through extra, which takes the place of the one user message askModel sends
  const got = await askModel(model, key, messages[1].content, SCHEMA, allowed, fullTry, {messages: messages}, "ideas");
  if(got.fail) return got;
  if(!got.out || typeof got.out !== "object" || !Array.isArray(got.out.ideas)){
    return {fail: Object.assign({stage: got.stopped ? "bad_shape" : "blocked"}, got.seen)};
  }
  return {ideas: got.out.ideas};
}

export default {
  async fetch(request){
    if(request.method !== "POST") return fail(405, "method");
    const key = process.env.OPENROUTER_API_KEY;
    if(!key) return fail(503, "not_set_up");

    let raw = "";
    try { raw = await request.text(); } catch(e){ raw = ""; }
    if(raw.length > MAX_BODY) return fail(413, "too_large");
    let body = null;
    try { body = JSON.parse(raw); } catch(e){ body = null; }
    if(!body || typeof body !== "object" || !contextOk(body.context)) return fail(400, "bad_request");
    const context = body.context;
    // mode "one": a single idea; anything else is the 3 ideas, as before
    const one = body.mode === "one" ? oneOk(body) : null;
    if(body.mode !== undefined && body.mode !== "one") return fail(400, "bad_request");
    if(body.mode === "one" && !one) return fail(400, "bad_request");
    const wanted = one ? 1 : IDEAS;

    const sizes = Object.assign({menu: context.own_menu.length, chars: raw.length}, one ? {mode: one.change ? "change" : "one"} : {});
    const asked = await askChain(TIME, (model, allowed, fullTry) => askOne(model, context, key, allowed, fullTry, one),
      (failed, model, tryNumber, tryMs) => console.error(JSON.stringify(Object.assign({}, failed, {model: model.id, try: tryNumber, ms: tryMs}, sizes))), CHAIN);
    const got = asked.got;
    if(got.fail){
      // the browser is told which stage, never OpenRouter's words
      const error = isBusy(got.fail) ? "busy" : isLimit(got.fail) ? "limit" : "failed";
      return reply(error === "busy" ? 503 : error === "limit" ? 429 : 502, {error: error, message: MESSAGES[error], ref: refOf(got.fail)});
    }

    const names = new Map(context.own_menu.map(r => [r.name.trim().toLowerCase(), r.name.trim()]));
    const ids = idsIn(context, new Set());
    const ideas = got.ideas.map(i => cleanIdea(i, names, ids)).filter(Boolean).slice(0, wanted);
    console.log(JSON.stringify(Object.assign({model: asked.model.id, ms: asked.ms, ideas: ideas.length, dropped: got.ideas.length - ideas.length}, sizes,
                                             asked.tries > 1 ? {tries: asked.tries} : {})));
    if(!ideas.length) return reply(502, {error: "failed", message: MESSAGES.failed, ref: "no_ideas"});
    return reply(200, {ideas: ideas});
  }
};
