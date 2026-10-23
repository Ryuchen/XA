const { config } = require('./config');

// Keep presentation artwork on the API's approved HTTPS asset origin.
// Content hashes preserve cache correctness without changing the image pixels.
function brandAssets() {
  const base = config().mediaBase + '/static/miniapp/brand/';
  return {
    homeMascotArt: base + 'xingan-girl.b9152bde9a75.webp',
    homeBirthdayArt: base + 'birthday-mouse.471ce68ded54.webp',
    companionshipArt: base + 'companionship.ef8c5df7748b.png',
    giftMomentArt: base + 'gift-moment.621950ab1523.png',
  };
}

module.exports = { brandAssets };
