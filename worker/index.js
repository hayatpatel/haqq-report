// Haqq Report: Telegram control bot as a Cloudflare Worker webhook.
// Commands run instantly and cost zero GitHub Actions minutes (plan fix #4).
//
// Secrets (set with `wrangler secret put`, never in this file):
//   TELEGRAM_BOT_TOKEN, TELEGRAM_OWNER_ID, TELEGRAM_WEBHOOK_SECRET, SUPABASE_URL, SUPABASE_KEY
//
// Security: only messages from TELEGRAM_OWNER_ID are obeyed, and Telegram must send the matching
// X-Telegram-Bot-Api-Secret-Token header (set when registering the webhook).

const HELP = [
  "/status  show mode and pause state",
  "/pause  /resume  stop or restart all posting",
  "/auto  /review  switch mode (Red always needs approval)",
  "/approve ID  /reject ID  decide a Red-lane draft",
  "/help",
].join("\n");

async function sb(env, path, init = {}) {
  const res = await fetch(`${env.SUPABASE_URL}/rest/v1/${path}`, {
    ...init,
    headers: {
      apikey: env.SUPABASE_KEY,
      // New-style sb_ keys are not JWTs: apikey only. Legacy JWT keys also need Bearer.
      ...(env.SUPABASE_KEY.startsWith("sb_") ? {} : { Authorization: `Bearer ${env.SUPABASE_KEY}` }),
      "Content-Type": "application/json",
      ...(init.headers || {}),
    },
  });
  if (!res.ok) throw new Error(`supabase ${res.status}`);
  const text = await res.text();
  return text ? JSON.parse(text) : null;
}

const setSetting = (env, key, value) =>
  sb(env, "settings?on_conflict=key", {
    method: "POST",
    headers: { Prefer: "resolution=merge-duplicates" },
    body: JSON.stringify({ key, value }),
  });

const decide = (env, id, status) =>
  sb(env, `drafts?id=eq.${encodeURIComponent(id)}&status=eq.pending`, {
    method: "PATCH",
    headers: { Prefer: "return=representation" },
    body: JSON.stringify({ status, decided_at: new Date().toISOString() }),
  });

async function handle(env, text) {
  const [cmd, arg] = text.trim().split(/\s+/);
  switch (cmd.split("@")[0]) {
    case "/help":
    case "/start":
      return HELP;
    case "/status": {
      const rows = await sb(env, "settings?select=key,value");
      return rows.map((r) => `${r.key}: ${r.value}`).join("\n") || "no settings";
    }
    case "/pause":
      await setSetting(env, "paused", "true");
      return "Posting paused.";
    case "/resume":
      await setSetting(env, "paused", "false");
      return "Posting resumed.";
    case "/auto":
      await setSetting(env, "mode", "auto");
      return "Mode: auto (Green and Amber post automatically; Red still needs approval).";
    case "/review":
      await setSetting(env, "mode", "review");
      return "Mode: review (every draft needs approval).";
    case "/approve":
    case "/reject": {
      if (!/^\d+$/.test(arg || "")) return `Usage: ${cmd} <draft id>`;
      const status = cmd === "/approve" ? "approved" : "rejected";
      const rows = await decide(env, arg, status);
      return rows && rows.length ? `Draft ${arg} ${status}.` : `No pending draft ${arg}.`;
    }
    default:
      return "Unknown command. /help";
  }
}

async function reply(env, chatId, text) {
  await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendMessage`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ chat_id: chatId, text }),
  });
}

export default {
  async fetch(request, env) {
    if (request.method !== "POST") return new Response("ok");
    if (request.headers.get("X-Telegram-Bot-Api-Secret-Token") !== env.TELEGRAM_WEBHOOK_SECRET) {
      return new Response("forbidden", { status: 403 });
    }
    const update = await request.json();
    const msg = update.message;
    if (!msg || !msg.text) return new Response("ok");
    if (String(msg.from?.id) !== String(env.TELEGRAM_OWNER_ID)) return new Response("ok"); // ignore strangers
    try {
      await reply(env, msg.chat.id, await handle(env, msg.text));
    } catch (err) {
      await reply(env, msg.chat.id, "Command failed. Check the Worker logs.");
    }
    return new Response("ok");
  },
};
