const test = require('node:test');
const assert = require('node:assert/strict');

test('message center opens notifications and can switch to and from the club feed', () => {
  const previous = global.Page;
  let definition;
  global.Page = page => { definition = page; };
  try {
    const file = require.resolve('../apps/boss-miniapp/native/pages/messages/index.js');
    delete require.cache[file];
    require(file);
    const page = {
      data: structuredClone(definition.data),
      setData(patch) { Object.assign(this.data, patch); },
    };
    assert.equal(page.data.section, 'private');
    const notificationItems = page.data.items;
    definition.section.call(page, { currentTarget: { dataset: { section: 'feed' } } });
    assert.equal(page.data.section, 'feed');
    definition.section.call(page, { currentTarget: { dataset: { section: 'private' } } });
    assert.equal(page.data.section, 'private');
    assert.equal(page.data.items, notificationItems);
    definition.section.call(page, { currentTarget: { dataset: { section: 'invalid' } } });
    assert.equal(page.data.section, 'private');
  } finally {
    global.Page = previous;
  }
});
