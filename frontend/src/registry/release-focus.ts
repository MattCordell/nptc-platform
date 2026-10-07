/**
 * Call just before navigating away from a form that was submitted. The submit
 * button still holds focus when the route changes, so the app's
 * focus-main-on-navigation rule leaves focus alone, and it then falls to
 * <body> as the button unmounts. Blurring first lets that rule move focus to
 * <main>.
 */
export function releaseFocus(): void {
  if (document.activeElement instanceof HTMLElement) {
    document.activeElement.blur();
  }
}
