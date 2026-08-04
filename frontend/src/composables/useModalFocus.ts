import { nextTick, onUnmounted, watch, type Ref } from 'vue'

const FOCUSABLE = 'button:not([disabled]), a[href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'

/** Lightweight focus trap for Teleport dialogs/sheets, with Escape close and focus return. */
export function useModalFocus(open: Ref<boolean>, close: () => void): void {
  let previouslyFocused: HTMLElement | null = null
  let releaseTimer: ReturnType<typeof setTimeout> | null = null
  function keydown(event: KeyboardEvent): void {
    if (!open.value) return
    if (event.key === 'Escape') { event.preventDefault(); close(); return }
    if (event.key !== 'Tab') return
    const layers = [...document.querySelectorAll<HTMLElement>('.dialog-layer, .drawer-layer')]
    const layer = layers.at(-1)
    if (layer === undefined) return
    const focusable = [...layer.querySelectorAll<HTMLElement>(FOCUSABLE)].filter((item) => item.offsetParent !== null)
    if (focusable.length === 0) { event.preventDefault(); return }
    const first = focusable[0], last = focusable.at(-1)!
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus() }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
  }
  watch(open, async (value) => {
    if (value) {
      if (releaseTimer !== null) { clearTimeout(releaseTimer); releaseTimer = null }
      previouslyFocused = document.activeElement instanceof HTMLElement ? document.activeElement : null
      document.body.classList.add('modal-open')
      document.addEventListener('keydown', keydown)
      await nextTick()
      const layers=[...document.querySelectorAll<HTMLElement>('.dialog-layer, .drawer-layer')]
      layers.at(-1)?.querySelector<HTMLElement>(FOCUSABLE)?.focus()
    } else {
      document.removeEventListener('keydown', keydown)
      releaseTimer = setTimeout(() => {
        if (document.querySelector('.dialog-layer, .drawer-layer') === null) document.body.classList.remove('modal-open')
        releaseTimer = null
      })
      previouslyFocused?.focus()
      previouslyFocused=null
    }
  }, { immediate: true })
  onUnmounted(() => {
    document.removeEventListener('keydown', keydown)
    if (releaseTimer !== null) clearTimeout(releaseTimer)
    if (open.value && document.querySelector('.dialog-layer, .drawer-layer') === null) document.body.classList.remove('modal-open')
  })
}
