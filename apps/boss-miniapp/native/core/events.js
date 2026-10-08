const listeners = {};
function on(name, handler) {
  (listeners[name] || (listeners[name] = new Set())).add(handler);
  return () => listeners[name] && listeners[name].delete(handler);
}
function emit(name, payload) {
  (listeners[name] || []).forEach(handler => handler(payload));
}
module.exports = { on, emit };
