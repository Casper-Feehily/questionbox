function wrapText(context, text, width, maximum) {
  const lines = [];
  let line = '';
  for (const character of Array.from(text.replace(/\s+/g, ' ').trim())) {
    if (line && context.measureText(line + character).width > width) {
      lines.push(line); line = character;
    } else line += character;
  }
  if (line) lines.push(line);
  if (lines.length <= maximum) return lines;
  const result = lines.slice(0, maximum);
  let last = Array.from(result[maximum - 1]);
  while (last.length && context.measureText(last.join('') + '…').width > width) last.pop();
  result[maximum - 1] = last.join('') + '…';
  return result;
}

function rounded(context, x, y, width, height, radius, color, stroke = false) {
  context.beginPath();
  context.moveTo(x + radius, y);
  context.lineTo(x + width - radius, y); context.quadraticCurveTo(x + width, y, x + width, y + radius);
  context.lineTo(x + width, y + height - radius); context.quadraticCurveTo(x + width, y + height, x + width - radius, y + height);
  context.lineTo(x + radius, y + height); context.quadraticCurveTo(x, y + height, x, y + height - radius);
  context.lineTo(x, y + radius); context.quadraticCurveTo(x, y, x + radius, y);
  context.closePath(); context.fillStyle = color; context.fill();
  if (stroke) { context.strokeStyle = '#24221f'; context.lineWidth = 4; context.stroke(); }
}

function drawQuestionCover(canvas, text) {
  canvas.width = 800; canvas.height = 800;
  const context = canvas.getContext('2d');
  context.textBaseline = 'top';
  context.fillStyle = '#f4f255'; context.fillRect(0, 0, 800, 800);
  context.fillStyle = '#24221f'; context.font = 'bold 34px sans-serif';
  context.fillText('QUESTIONBOX', 64, 100);
  rounded(context, 64, 172, 684, 502, 22, '#24221f');
  rounded(context, 52, 160, 684, 502, 22, '#fffdf6', true);
  rounded(context, 90, 196, 100, 40, 20, '#ed91bb');
  context.fillStyle = '#341b2a'; context.font = 'bold 23px sans-serif'; context.fillText('Q / A', 109, 204);
  context.fillStyle = '#24221f'; context.font = 'bold 44px serif';
  wrapText(context, text, 608, 6).forEach((line, index) => context.fillText(line, 90, 262 + index * 56));
  context.fillStyle = '#626044'; context.font = '24px sans-serif';
  context.fillText('读一封认真回信 ↗', 90, 616);
  context.fillStyle = '#24221f'; context.font = '25px serif';
  context.fillText('慢慢提问，认真回答。', 64, 698);
}

function createQuestionCover(page, question) {
  return new Promise((resolve, reject) => {
    wx.createSelectorQuery().in(page).select('#question-share-canvas').fields({ node: true }).exec(result => {
      try {
        const canvas = result[0] && result[0].node;
        if (!canvas) throw new Error('Share canvas is not available');
        drawQuestionCover(canvas, question.text);
        wx.canvasToTempFilePath({ canvas, width: 800, height: 800, destWidth: 800, destHeight: 800, fileType: 'png',
          success: result => result.tempFilePath ? resolve(result.tempFilePath) : reject(new Error('Missing share image')),
          fail: reject }, page);
      } catch (error) { reject(error); }
    });
  });
}

const questionCoverKey = question => JSON.stringify([question.id, question.text]);
module.exports = { wrapText, drawQuestionCover, createQuestionCover, questionCoverKey };
