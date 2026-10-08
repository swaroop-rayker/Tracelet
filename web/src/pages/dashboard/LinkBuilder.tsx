/**
 * Share this link (F1.AC12, DESIGN §12 E36, §16 M7.7): the link's capture URL with optional
 * campaign tags, to copy, and as a QR code drawn in the browser (ADR-0023), to download.
 *
 * Its own lazy chunk, with the encoder, so a link's page pays for it only when it renders.
 * Nothing is stored and no request is made: the destination is the link's row, as always
 * (invariant 3), and only `utm_*` keys are added (invariant 7).
 */

import { useMemo, useState } from 'react';
import { Button, Card, CopyButton, Field } from '@/components/ui';
import { encodeQr, qrSvg } from '@/qr';
import { UTM_KEYS, shareUrl, type UtmKey } from '@/share';

const LABEL: Readonly<Record<UtmKey, string>> = {
  utm_source: 'Source',
  utm_medium: 'Medium',
  utm_campaign: 'Campaign',
  utm_term: 'Term',
  utm_content: 'Content',
};

const PLACEHOLDER: Readonly<Record<UtmKey, string>> = {
  utm_source: 'instagram',
  utm_medium: 'social',
  utm_campaign: 'diwali',
  utm_term: '',
  utm_content: 'bio',
};

function download(svg: string, slug: string): void {
  const url = URL.createObjectURL(new Blob([svg], { type: 'image/svg+xml' }));
  const a = document.createElement('a');
  a.href = url;
  a.download = `tracelet-${slug}-qr.svg`;
  a.click();
  URL.revokeObjectURL(url);
}

export default function LinkBuilder({
  captureUrl,
  slug,
}: {
  readonly captureUrl: string;
  readonly slug: string;
}): React.JSX.Element {
  const [tags, setTags] = useState<Partial<Record<UtmKey, string>>>({});
  const url = shareUrl(captureUrl, tags);
  const svg = useMemo(() => qrSvg(encodeQr(url)), [url]);
  const src = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
  return (
    <Card
      title="Share this link"
      description="Add campaign tags to see where visits came from on Sources. Empty fields add nothing; the link still goes where it always does."
    >
      <div className="stack">
        <div className="link-builder__fields">
          {UTM_KEYS.map((key) => (
            <Field
              key={key}
              label={LABEL[key]}
              value={tags[key] ?? ''}
              onChange={(value) => {
                setTags({ ...tags, [key]: value });
              }}
              maxLength={100}
              placeholder={PLACEHOLDER[key]}
              required={false}
            />
          ))}
        </div>
        <div className="link-builder__url">
          <span className="mono link-builder__text">{url}</span>
          <CopyButton value={url} label="Copy the share URL" />
        </div>
        <div className="link-builder__qr">
          <img className="qr" src={src} alt={`QR code for ${url}`} width={160} height={160} />
          <div className="stack-sm">
            <p className="t-secondary m-0">Scans to exactly the URL above.</p>
            <div>
              <Button
                variant="secondary"
                icon="Download"
                onClick={() => {
                  download(svg, slug);
                }}
              >
                Download SVG
              </Button>
            </div>
          </div>
        </div>
      </div>
    </Card>
  );
}
