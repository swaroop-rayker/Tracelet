/**
 * Labels and number formatting, in one place so a key reads the same on every panel.
 *
 * Country names come from `Intl.DisplayNames`, so no country list ships with the app.
 * Unknown keys are never shown as an empty string: the API reports an abstention as
 * `''`, and it is rendered as the words that explain it.
 */

const regions = new Intl.DisplayNames(['en'], { type: 'region' });

export function countryName(code: string): string {
  if (code === '') return 'Unknown';
  try {
    return regions.of(code.toUpperCase()) ?? code;
  } catch {
    return code;
  }
}

const CLASSIFICATION_LABEL: Readonly<Record<string, string>> = {
  human: 'Human',
  bot: 'Bot',
  crawler: 'Crawler',
  datacenter: 'Datacenter',
  spam: 'Spam',
  spoofed: 'Spoofed',
  unknown: 'Unclassified',
};

const TITLE: Readonly<Record<string, string>> = {
  ...CLASSIFICATION_LABEL,
  server: 'Pending',
  enriched: 'Enriched',
  server_only: 'Server only',
  rate_limited: 'Rate-limited',
  mobile: 'Mobile',
  tablet: 'Tablet',
  desktop: 'Desktop',
  tv: 'TV',
  broadband: 'Broadband',
  vpn_suspected: 'VPN suspected',
  tor: 'Tor',
  business: 'Business',
  browser: 'Browser',
  none: 'None',
  gps: 'GPS (consented)',
  geolite2: 'GeoLite2',
  ip2location: 'IP2Location',
  ipinfo: 'IPinfo',
  dbip: 'DB-IP',
  rdns: 'Reverse DNS',
  asn_org: 'ASN organisation',
  cf_colo: 'Cloudflare edge',
  external_api: 'ipwho.is',
  timezone: 'Timezone',
  country: 'Country',
  admin1: 'State',
  admin2: 'District',
  city: 'City',
};

/** A human label for an enum-like key from the API. */
export function label(key: string): string {
  if (key === '') return 'Unknown';
  return TITLE[key] ?? key.replace(/_/g, ' ');
}

export const DIMENSION_LABEL: Readonly<Record<string, string>> = {
  country: 'Country',
  admin1: 'State',
  city: 'City',
  asn: 'ASN',
  isp: 'ISP',
  device_class: 'Device class',
  browser: 'Browser',
  app_medium: 'App or browser',
  os: 'Operating system',
  screen: 'Screen resolution',
  connection_class: 'Connection',
  classification: 'Classification',
};

/** A breakdown row's label: qualified location keys become readable. */
export function dimensionValue(dimension: string, key: string): string {
  if (key === '') return 'Unknown';
  if (dimension === 'country') return countryName(key);
  if (dimension === 'admin1' || dimension === 'city') {
    const [country = '', ...rest] = key.split('|');
    return `${rest.join(', ')} (${country})`;
  }
  if (dimension === 'asn') return `AS${key}`;
  if (
    dimension === 'device_class' ||
    dimension === 'connection_class' ||
    dimension === 'classification'
  ) {
    return label(key);
  }
  return key;
}

export function count(value: number | null): string {
  return value === null ? '—' : value.toLocaleString();
}

export function pct(value: number | null, digits = 0): string {
  return value === null ? '—' : `${(value * 100).toFixed(digits)}%`;
}

/** A timestamp in the admin's display timezone (their preference, not the reporting zone). */
export function when(iso: string, zone: string, withSeconds = false): string {
  return new Date(iso).toLocaleString('en-IN', {
    timeZone: zone,
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    ...(withSeconds ? { second: '2-digit' } : {}),
  });
}

export function day(iso: string, zone: string): string {
  return new Date(iso).toLocaleDateString('en-IN', {
    timeZone: zone,
    weekday: 'short',
    month: 'short',
    day: 'numeric',
  });
}
