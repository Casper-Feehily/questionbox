// Public reading history is separate from animation state: cancelling a drag never changes the letter.
function shuffled(ids, random, last) {
  const result = ids.slice();
  for (let i = result.length - 1; i > 0; i--) {
    const j = Math.floor(random() * (i + 1));
    [result[i], result[j]] = [result[j], result[i]];
  }
  if (result.length > 1 && result[0] === last) [result[0], result[1]] = [result[1], result[0]];
  return result;
}

class Reader {
  constructor(fetchPage, random = Math.random) {
    this.fetchPage = fetchPage;
    this.random = random;
    this.pool = [];
    this.sequence = [];
    this.questions = new Map();
    this.tones = new Map();
    this.index = 0;
    this.page = 0;
    this.hasMore = true;
    this.total = 0;
    this.pending = null;
  }
  async load() {
    if (this.pending) return this.pending;
    if (!this.hasMore) return;
    this.pending = (async () => {
      const result = await this.fetchPage(this.page + 1);
      const added = [];
      for (const question of result.items) {
        if (!this.questions.has(question.id)) {
          this.tones.set(question.id, ['lavender', 'green', 'blue'][this.pool.length % 3]);
          this.pool.push(question.id);
          added.push(question.id);
        }
        this.questions.set(question.id, question);
      }
      this.sequence.push(...shuffled(added, this.random));
      this.page++;
      this.hasMore = result.has_more;
      this.total = result.count;
    })();
    try { await this.pending; } finally { this.pending = null; }
  }
  async plan(direction) {
    if (direction < 0) return this.index > 0 ? this.index - 1 : null;
    while (this.hasMore && this.sequence.length <= this.index + 3) await this.load();
    if (this.pool.length < 2) return null;
    while (this.sequence.length <= this.index + 3) {
      this.sequence.push(...shuffled(this.pool, this.random, this.sequence[this.sequence.length - 1]));
    }
    return this.index + 1;
  }
  commit(index) { if (index !== null) this.index = index; }
  visible(at = this.index) {
    const cards = [], seen = new Set();
    for (let position = at; position < Math.min(at + 3, this.sequence.length); position++) {
      const id = this.sequence[position];
      if (seen.has(id)) continue;
      seen.add(id);
      cards.push(this.card(position, cards.length));
    }
    if (at > 0) cards.push(this.card(at - 1, -1));
    return cards;
  }
  card(position, slot) {
    const id = this.sequence[position];
    return { key: `letter-${position}`, question: this.questions.get(id), tone: this.tones.get(id), slot };
  }
  async shuffle() {
    if (this.pool.length < 2 && this.hasMore) await this.load();
    if (this.pool.length < 2) return false;
    this.sequence = shuffled(this.pool, this.random, this.sequence[this.index]);
    this.index = 0;
    return true;
  }
}

function motion(cards, width, dx = 0) {
  const styles = {};
  const progress = Math.max(0, Math.min(1, dx / width));
  for (const card of cards) {
    let x = 0, y = 0, scale = 1, angle = 0, opacity = 1, z = 4;
    if (card.slot === -1) { x = -width + Math.max(0, Math.min(width, dx)); opacity = dx > 0 ? 1 : 0; z = 5; }
    if (card.slot === 1) { y = 27; scale = .965; angle = -1; z = 3; }
    if (card.slot === 2) { y = 53; scale = .925; angle = 1; z = 2; }
    if (dx > 0 && cards.some(item => item.slot === -1)) {
      if (card.slot === 0) { y = 27 * progress; scale -= .035 * progress; angle = -progress; }
      if (card.slot === 1) { y += 26 * progress; scale -= .04 * progress; angle += 2 * progress; }
      if (card.slot === 2) { y += 15 * progress; scale -= .035 * progress; opacity = 1 - progress; }
    } else if (dx < 0 && card.slot === 0) {
      x = Math.max(-190, dx * .65); angle = x * .008;
    }
    styles[card.key] = `transform:translate3d(${x}px,${y}px,0) scale(${scale}) rotate(${angle}deg);opacity:${opacity};z-index:${z};`;
  }
  return styles;
}

module.exports = { Reader, motion };
