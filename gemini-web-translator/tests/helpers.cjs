const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '..', 'gemini-web-translator.js'), 'utf8');
const instrumented = source.replace('    if (isExcludedContext()) {', `
    globalThis.api = {CONFIG, state, requireIsolatedRuntime, configureApiKey, configureOpenAIBaseUrl, init, startTranslationProcess};
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
module.exports = { source, instrumented, load };
