const puppeteer = require('puppeteer');
const path = require('path');

const htmlPath = path.resolve(__dirname, 'one-pager-augusto.html');
const pdfPath  = path.resolve(__dirname, 'one-pager-augusto.pdf');

(async () => {
  const browser = await puppeteer.launch({
    headless: true,
    args: ['--no-sandbox', '--disable-setuid-sandbox'],
  });
  const page = await browser.newPage();
  await page.goto('file://' + htmlPath, { waitUntil: 'networkidle0' });
  await page.pdf({
    path: pdfPath,
    format: 'A4',
    printBackground: true,
    margin: { top: '0mm', right: '0mm', bottom: '0mm', left: '0mm' },
    preferCSSPageSize: true,
  });
  await browser.close();
  console.log('PDF gerado:', pdfPath);
})();
