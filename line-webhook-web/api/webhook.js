const crypto = require("node:crypto");

const CHANNEL_SECRET = process.env.LINE_CHANNEL_SECRET;
const ACCESS_TOKEN = process.env.LINE_CHANNEL_ACCESS_TOKEN;

// Vercel doesn't run body-parser middleware on this route, so we read the
// raw bytes ourselves — signature verification must hash the exact bytes
// LINE signed, not a re-serialized JSON.parse(...) copy.
function readRawBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    req.on("data", (chunk) => chunks.push(chunk));
    req.on("end", () => resolve(Buffer.concat(chunks)));
    req.on("error", reject);
  });
}

function isValidSignature(rawBody, signature) {
  if (!signature) return false;
  const expected = crypto
    .createHmac("sha256", CHANNEL_SECRET)
    .update(rawBody)
    .digest("base64");
  return crypto.timingSafeEqual(Buffer.from(expected), Buffer.from(signature));
}

async function replyMessage(replyToken, text) {
  await fetch("https://api.line.me/v2/bot/message/reply", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${ACCESS_TOKEN}`,
    },
    body: JSON.stringify({
      replyToken,
      messages: [{ type: "text", text }],
    }),
  });
}

module.exports = async (req, res) => {
  if (req.method !== "POST") {
    res.status(405).send("Method Not Allowed");
    return;
  }

  if (!CHANNEL_SECRET || !ACCESS_TOKEN) {
    console.error("Missing LINE_CHANNEL_SECRET or LINE_CHANNEL_ACCESS_TOKEN");
    res.status(500).send("Server not configured");
    return;
  }

  const rawBody = await readRawBody(req);
  const signature = req.headers["x-line-signature"];

  if (!isValidSignature(rawBody, signature)) {
    res.status(401).send("Invalid signature");
    return;
  }

  const { events = [] } = JSON.parse(rawBody.toString("utf8"));

  await Promise.all(
    events.map((event) => {
      if (event.type === "message" && event.message?.type === "text") {
        return replyMessage(event.replyToken, event.message.text);
      }
      return Promise.resolve();
    })
  );

  // LINE only requires a 200 response; it doesn't read the body.
  res.status(200).send("OK");
};

module.exports.config = {
  api: {
    bodyParser: false,
  },
};
