const assert = require('node:assert/strict');
const test = require('node:test');
const { load, textNode, prepare } = require('./helpers.cjs');

function records(entry, translation = '译文') {
    return new Map([[entry.cacheKey, { key: entry.cacheKey, translation }]]);
}

test('similar URLs never authorize translations for different original text', () => {
    for (const [first, second] of [
        ['https://example.test/article?id=1', 'https://example.test/article?id=2'],
        ['https://example.test/articles/100', 'https://example.test/articles/101'],
        ['https://example.test/#/one', 'https://example.test/#/two']
    ]) {
        const { api } = load();
        const old = api.generateTranslationEntries([textNode('First article')])[0];
        api.setCache(first, records(old));
        const target = textNode('Different article');
        const entries = api.generateTranslationEntries([target]);
        const result = api.applyCachedTranslations(entries, api.getCache(second));
        assert.equal(target.nodeValue, 'Different article');
        assert.equal(result.pending.length, 1);
        assert.equal(result.hits, 0);
    }
});

test('cache maps original text/context, not changing positional IDs, and leaves misses pending', () => {
    const { api } = load();
    const old = api.generateTranslationEntries([textNode('Hello')])[0];
    const url = 'https://example.test/articles/100';
    api.setCache(url, records(old));
    const nodes = [textNode('New heading'), textNode('Hello')];
    const entries = api.generateTranslationEntries(nodes);
    const result = api.applyCachedTranslations(entries, api.getCache(url));
    assert.equal(nodes[0].nodeValue, 'New heading');
    assert.equal(nodes[1].nodeValue, '译文');
    assert.equal(result.pending[0], entries[0]);
    assert.equal(result.hits, 1);
    assert.equal(api.restoreOriginalText(), 1);
    assert.equal(nodes[1].nodeValue, 'Hello');
});

test('context, language, profile and origin prevent inappropriate reuse', () => {
    const { api } = load();
    const url = 'https://example.test/articles/100';
    const old = api.generateTranslationEntries([textNode('Bank', 'River bank')])[0];
    api.setCache(url, records(old), 'English', 'model-a');
    assert.equal(api.getCache(url, 'English', 'model-b').size, 0);
    assert.equal(api.getCache(url, 'Japanese', 'model-a').size, 0);
    assert.equal(api.getCache('https://example.tast/articles/100', 'English', 'model-a').size, 0);
    const entries = api.generateTranslationEntries([textNode('Bank', 'Bank account')]);
    assert.equal(api.applyCachedTranslations(entries, api.getCache(url, 'English', 'model-a')).hits, 0);
});

test('intentional unchanged records are reusable and legacy positional caches are invalidated', () => {
    const { api, storage } = load({ gm_cache_meta: JSON.stringify({ gm_cache_v7_old: { size: 10 } }), gm_cache_v7_old: 'P_0_0,"bad"' });
    const node = textNode('Already translated');
    const entry = api.generateTranslationEntries([node])[0];
    api.setCache('https://example.test/', records(entry, null));
    const result = api.applyCachedTranslations([entry], api.getCache('https://example.test/'));
    assert.equal(result.hits, 1);
    assert.equal(result.updated, 0);
    assert.equal(node.nodeValue, 'Already translated');
    assert.equal(storage.has('gm_cache_v7_old'), false);
});

test('cache replacement counts only the replacement; oversized records do not evict other pages', () => {
    const { api, storage } = load();
    const entry = api.generateTranslationEntries([textNode('Hello')])[0];
    const data = records(entry);
    api.setCache('https://example.test/one', data);
    const oneSize = api.getCacheStats().totalSize;
    storage.set('gm_max_cache_size', oneSize * 2);
    api.setCache('https://example.test/two', data);
    api.setCache('https://example.test/one', data);
    assert.equal(api.getCacheStats().count, 2);
    assert.equal(api.setCache('https://example.test/large', records(entry, 'x'.repeat(oneSize * 3))), false);
    assert.equal(api.getCacheStats().count, 2);
    assert.ok(api.getCacheStats().totalSize <= oneSize * 2);
});

test('expired cache is removed and another valid candidate can still supply the original', () => {
    const { api, storage } = load({ gm_cache_ttl: 1000 });
    const entry = api.generateTranslationEntries([textNode('Hello')])[0];
    const url = 'https://example.test/articles/100';
    api.setCache(url, records(entry, 'old'));
    api.setCache('https://example.test/articles/101', records(entry, 'new'));
    const meta = api.getCacheMeta();
    meta[api.generateCacheKey(url)].timestamp = Date.now() - 2000;
    storage.set(api.CONFIG.cacheMetaKey, JSON.stringify(meta));
    assert.equal(api.getCache(url).get(entry.cacheKey).translation, 'new');
    assert.equal(api.getCacheStats().count, 1);
});

test('translation and restore preserve external DOM changes and detached nodes', () => {
    const { api } = load();
    const nodes = [textNode('First'), textNode('Second'), textNode('Third')];
    const { entries } = prepare(api, nodes);
    nodes[0].nodeValue = 'Live update';
    nodes[1].isConnected = false;
    assert.equal(api.applyPreparedTranslation({ id: entries[0].id, text: 'stale' }), 0);
    assert.equal(api.applyPreparedTranslation({ id: entries[1].id, text: 'detached' }), 0);
    assert.equal(api.applyPreparedTranslation({ id: entries[2].id, text: '译文' }), 1);
    nodes[2].nodeValue = 'Newer update';
    assert.equal(api.restoreOriginalText(), 0);
    assert.deepEqual(nodes.map(node => node.nodeValue), ['Live update', 'Second', 'Newer update']);
});

test('foreign IDs, malformed quotes, duplicates, reordering and data after finish reject atomically', () => {
    for (const output of [
        'P_1_1,"foreign"\n', 'P_0_0,unquoted\n', 'P_0_0,"bad "quote""\n',
        'P_0_0,"one"\nP_0_0,"two"\n', '__FINISH__,"wrong"\n',
        '__FINISH__,"finish"\nP_0_0,"late"\n'
    ]) {
        const { api } = load();
        const { nodes, batches } = prepare(api, [textNode('First'), textNode('Second')]);
        assert.throws(() => api.appendBatchTranslationChunk(batches[0], output, 1));
        assert.equal(nodes[0].nodeValue, 'First');
        assert.equal(nodes[1].nodeValue, 'Second');
        assert.equal(batches[0].appliedIds.size, 0);
    }
});

test('valid streamed records count omitted, unchanged and stale nodes consistently', () => {
    const { api } = load();
    const { nodes, entries, batches } = prepare(api, [textNode('First'), textNode('Second'), textNode('Third')], 3);
    nodes[1].nodeValue = 'Updated externally';
    const output = `${entries[1].id},"stale"\n${entries[2].id},"译文"\n__FINISH__,"finish"\n`;
    const result = api.appendBatchTranslationChunk(batches[0], output, 1);
    assert.equal(result.appliedCount, 1);
    assert.equal(result.skippedCount, 2);
    assert.equal(api.resetBatchForRetry(batches[0]), 1);
    assert.deepEqual(nodes.map(node => node.nodeValue), ['First', 'Updated externally', 'Third']);
});
