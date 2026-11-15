const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '..', 'gemini-web-translator.js'), 'utf8');
const exportsList = `CONFIG, state, getPlatformModelSettings, getOpenAIProtocol, getOpenAIServiceTier,
    normalizeOpenAIBaseUrl, getRequestSettings, createStreamRequestOptions, extractStreamTextFromPayload,
    consumeSSEPayload, flushSSEBuffer, createTranslationBatches, createProgressState,
    translateSingleBatch, translateAllBatches, generateTranslationEntries, applyPreparedTranslation,
    restoreOriginalText, resetBatchForRetry, parseCsvTranslationLine, appendBatchTranslationChunk,
    normalizeUrl, generateCacheKey, getCacheMeta, getCache, setCache, applyCachedTranslations,
    cacheCompletedBatch, getCacheStats, clearAllCache, clearCurrentPageCache,
    getHttpError, getRetryDelay, waitForRetry, extractTextNodes,
    requireIsolatedRuntime, configureApiKey, configureOpenAIBaseUrl, init,
    openSettings, closeSettings, startTranslationProcess, retryFailedTranslation,
    cancelActiveTranslationAndRestoreOriginal, getToastContent`;
const instrumented = source.replace('    if (isExcludedContext()) {', `
    globalThis.api = {${exportsList}};
    return;
    if (isExcludedContext()) {`);

function load(values = {}, request = () => { throw new Error('Unexpected request'); }, globals = {}) {
    const storage = new Map(Object.entries(values));
    const menus = new Map();
    const context = vm.createContext({
        URL, Blob, TextDecoder, setTimeout, clearTimeout, console,
        window: { location: { href: 'https://example.test/articles/100', origin: 'https://example.test' } },
        GM_info: { sandboxMode: 'dom' },
        GM_getValue: (key, fallback) => storage.has(key) ? storage.get(key) : fallback,
        GM_setValue: (key, value) => storage.set(key, value),
        GM_deleteValue: key => storage.delete(key),
        GM_registerMenuCommand: (label, callback) => menus.set(label, callback),
        GM_xmlhttpRequest: request,
        ...globals
    });
    vm.runInContext(instrumented, context);
    return { api: context.api, storage, menus, context };
}

function textNode(text, context = text, tag = 'P') {
    return { nodeValue: text, isConnected: true, parentElement: { tagName: tag, textContent: context } };
}
function prepare(api, nodes = [textNode('Hello')], batchSize = 1, workers = 1) {
    const entries = api.generateTranslationEntries(nodes);
    const batches = api.createTranslationBatches(entries, batchSize);
    api.state.isTranslating = true;
    api.state.activeTranslationRunId = 1;
    api.state.translationProgress = api.createProgressState(entries.length, batches.length, workers);
    return { entries, batches, nodes };
}
function options(batch, extra = {}) {
    return { runId: 1, batch, platform: 'openai', apiKey: 'test-key', model: 'test-model', prompt: 'Translate', ...extra };
}
function sse(text) {
    return `data: ${JSON.stringify({ choices: [{ delta: { content: text } }] })}\n\n`;
}
module.exports = { source, instrumented, load, textNode, prepare, options, sse };
