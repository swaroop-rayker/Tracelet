/** The product mark: a rising trace on the accent. Colours come from the stylesheet. */
export function BrandMark(): React.JSX.Element {
  return (
    <svg className="brand-mark" viewBox="0 0 24 24" width="24" height="24" aria-hidden="true">
      <rect className="brand-mark__tile" width="24" height="24" rx="6" />
      <path
        className="brand-mark__trace"
        d="M5 15.5 9.5 11l3 3L19 7.5"
        fill="none"
        strokeWidth="2.2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <circle className="brand-mark__dot" cx="19" cy="7.5" r="1.8" />
    </svg>
  );
}
