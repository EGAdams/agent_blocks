/**
 * Application-side port for Toyota's editable conversation input.
 *
 * The concrete browser adapter must keep the visible control and any backing
 * transcript accumulator synchronized.  Clearing only the DOM element is not
 * sufficient: the next recognition callback would otherwise restore the old
 * committed prefix.
 */
export interface IInputDraft {
  getText(): string;
  replace(text: string): void;
  append(text: string): void;
  clear(): void;
}
