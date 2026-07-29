module.exports = (req, res) => {
  res.status(200).send("line-webhook-web is running. Webhook endpoint: /api/webhook");
};
