/**
 * QR codes for the link builder (F1.AC12, ADR-0023): the vendored Nayuki encoder behind two
 * typed functions. Pure -- no DOM -- so it is unit-tested directly.
 *
 * The SVG is black on white whatever the theme: a scanner needs contrast, not a palette. It is
 * a file to download or show as an `<img>`, never markup in the page (UI-2, UI-3).
 */

import qrcodegen from '@/vendor/qrcodegen';

export interface QrMatrix {
  readonly version: number;
  readonly size: number;
  /** `dark[y][x]`: whether the module at column x, row y is dark. */
  readonly dark: readonly (readonly boolean[])[];
}

/** Medium error correction: survives a smudged print and still keeps a share URL small. */
export function encodeQr(text: string): QrMatrix {
  const code = qrcodegen.QrCode.encodeText(text, qrcodegen.QrCode.Ecc.MEDIUM);
  const size = code.size;
  const dark = Array.from({ length: size }, (_, y) =>
    Array.from({ length: size }, (_, x) => code.getModule(x, y)),
  );
  return { version: code.version, size, dark };
}

/** A standalone SVG document: one path of dark modules, with a quiet zone of `border`. */
export function qrSvg(matrix: QrMatrix, border = 4): string {
  const span = matrix.size + border * 2;
  const cells: string[] = [];
  matrix.dark.forEach((row, y) => {
    row.forEach((on, x) => {
      if (on) cells.push(`M${String(x + border)},${String(y + border)}h1v1h-1z`);
    });
  });
  return (
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${String(span)} ${String(span)}" ` +
    `shape-rendering="crispEdges">` +
    `<rect width="100%" height="100%" fill="white"/>` +
    `<path d="${cells.join('')}" fill="black"/></svg>`
  );
}
