import { useEffect, useId, useRef } from "react";
import type { ReactNode } from "react";

type DialogProps = {
  open: boolean;
  onClose: () => void;
  /** Rendered as the dialog's heading and referenced by `aria-labelledby` -
   * every dialog must have one, there is no unlabelled fallback. */
  title: string;
  children: ReactNode;
};

const FOCUSABLE_SELECTOR =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

function getFocusable(dialog: HTMLDialogElement): HTMLElement[] {
  return Array.from(dialog.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR));
}

/**
 * A modal dialog (issue #148). Prefers the native `<dialog>` element's
 * `showModal()` where it is available - top-layer rendering, the implicit
 * `aria-modal="true"`, and a browser-level `Tab` focus trap for free - but
 * does not depend on it for the focus contract: `showModal()`/the browser's
 * own trap are not implemented in jsdom (nor, historically, in every real
 * browser release), so focus-in, `Tab`-trap, and focus-restore are all
 * handled explicitly here. That keeps the contract testable and consistent
 * regardless of what the platform does on top of it.
 *
 * Setup and its matching teardown live in one effect precisely so a single
 * `return` covers both ways a dialog stops being open: `open` flipping to
 * `false`, and the component unmounting outright while still open (e.g. a
 * parent that renders `{isOpen && <Dialog .../>}`, or a route change) -
 * both need the same close()/focus-restore behaviour, and a `return`
 * cleanup is the one place both paths run.
 */
export function Dialog({ open, onClose, title, children }: DialogProps) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const onCloseRef = useRef(onClose);
  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog || !open) {
      return;
    }

    const trigger = document.activeElement;

    // `showModal()` throws `InvalidStateError` if the `open` attribute is
    // already present, so the manual attribute below is only ever set when
    // showModal() did *not* run - never alongside it.
    let usedNativeModal = false;
    if (typeof dialog.showModal === "function") {
      try {
        dialog.showModal();
        usedNativeModal = true;
      } catch {
        // jsdom declares showModal but throws "not implemented" - fall
        // through to the plain `open` attribute below.
      }
    }
    if (!usedNativeModal) {
      dialog.setAttribute("open", "");
    }

    // Focus the first focusable descendant, or the dialog itself when it
    // has none - a content-only dialog (no button, no link) would
    // otherwise leave focus on <body>, unreachable by either Escape or Tab
    // once the listeners below are scoped to "inside the dialog".
    (getFocusable(dialog)[0] ?? dialog).focus();

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        // When showModal() is in effect, the browser's own `cancel` event
        // (handled below) already calls onClose - handling Escape here too
        // would fire it twice.
        if (!usedNativeModal) {
          event.preventDefault();
          onCloseRef.current();
        }
        return;
      }

      if (event.key !== "Tab") {
        return;
      }

      const focusable = getFocusable(dialog);
      if (focusable.length === 0) {
        event.preventDefault();
        return;
      }

      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const active = document.activeElement;

      if (event.shiftKey && active === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && active === last) {
        event.preventDefault();
        first.focus();
      }
    };

    // Bound to `document`, not the dialog itself: focus can legitimately
    // sit on the dialog element or even <body> (the no-focusable-content
    // case above), and a listener on the dialog would never see a keydown
    // that starts outside of it.
    document.addEventListener("keydown", handleKeyDown);

    const handleCancel = (event: Event) => {
      event.preventDefault();
      onCloseRef.current();
    };
    dialog.addEventListener("cancel", handleCancel);

    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      dialog.removeEventListener("cancel", handleCancel);

      if (typeof dialog.close === "function") {
        try {
          dialog.close();
        } catch {
          // Same jsdom gap as showModal above.
        }
      }
      dialog.removeAttribute("open");

      if (trigger instanceof HTMLElement) {
        trigger.focus();
      }
    };
  }, [open]);

  return (
    <dialog
      ref={dialogRef}
      tabIndex={-1}
      aria-labelledby={titleId}
      className="rounded-md border border-[var(--color-border)] bg-[var(--color-surface)] p-6 backdrop:bg-black/50"
    >
      {open ? (
        <>
          <h2 id={titleId} className="text-lg font-semibold text-[var(--color-text)]">
            {title}
          </h2>
          {children}
        </>
      ) : null}
    </dialog>
  );
}
