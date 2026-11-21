const assert = require('node:assert/strict');

module.exports = async function checkStreetAtlas(browser, url) {
  const page = await browser.newPage();
  try {
    for (const width of [390, 1440, 1920, 2560]) {
      await page.setViewportSize({width, height: 900});
      await page.goto(url + '/?demo=travel');
      await page.getByRole('button', {name: '贵阳', exact: true}).waitFor();
      const regions = page.locator('button.region-name');
      assert.equal(await regions.count(), 9);
      await page.locator('#task').fill('周末想在贵州慢慢走');
      await page.locator('#budget').fill('3100');
      for (let index = 0; index < 9; index++) {
        await regions.nth(index).click();
        await page.waitForFunction(() => document.querySelectorAll('.street-places button').length >= 3);
        await page.locator('.district-view .primary').click();
        await page.waitForFunction(() => document.querySelectorAll('.street-pin').length >= 5);
        await page.locator('.street-zoom button').first().click();
        await page.locator('.street-illustration summary').click();
        await page.locator('.street-illustration img').evaluate(img => img.decode());
        if(index === 0) {
          await page.locator('.street-places button').first().press('Enter');
          const details = page.locator(width === 390 ? '#mobile-poi' : '#poi-content');
          await details.getByRole('button', {name: /加入行程/}).click();
          assert.match(await page.locator('#destination-name').textContent(), /已选目的地/);
        }
        await page.getByRole('button', {name: '← 返回贵州地图'}).click();
        assert.equal(await page.locator('#task').inputValue(), '周末想在贵州慢慢走');
        assert.equal(await page.locator('#budget').inputValue(), '3100');
      }
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
    }
  } finally {
    await page.close();
  }
};

