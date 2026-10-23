import { useEffect } from 'react';

/** Taro's custom button hosts need native keyboard semantics on the web. */
export function useH5Controls() {
  useEffect(() => {
    if (process.env.TARO_ENV !== 'h5' || typeof document === 'undefined') return;
    const update = (button: Element) => {
      const disabled = button.hasAttribute('disabled') && button.getAttribute('disabled') !== 'false';
      if (!disabled && button.hasAttribute('disabled')) button.removeAttribute('disabled');
      button.setAttribute('role', 'button');
      button.setAttribute('tabindex', disabled ? '-1' : '0');
      button.setAttribute('aria-disabled', String(disabled));
    };
    document.querySelectorAll('taro-button-core').forEach(update);
    const observer = new MutationObserver(records => {
      for (const record of records) {
        if (record.type === 'attributes' && (record.target as Element).matches('taro-button-core')) update(record.target as Element);
        for (const node of Array.from(record.addedNodes)) if (node instanceof Element) {
          if (node.matches('taro-button-core')) update(node);
          node.querySelectorAll('taro-button-core').forEach(update);
        }
      }
    });
    observer.observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ['disabled'] });
    const onKey = (event: KeyboardEvent) => {
      if (!(event.target instanceof HTMLElement) || !event.target.matches('taro-button-core')) return;
      if (event.key !== 'Enter' && event.key !== ' ') return;
      event.preventDefault();
      if (event.target.getAttribute('aria-disabled') !== 'true') event.target.click();
    };
    document.addEventListener('keydown', onKey);
    return () => { observer.disconnect(); document.removeEventListener('keydown', onKey); };
  }, []);
}
