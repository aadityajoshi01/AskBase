const prefixes = { colors: '', fonts: 'font-', radii: 'radius-', shadows: 'shadow-', spacing: 'space-' };

export function applyThemeTokens(tokens, root = document.documentElement) {
  for (const [group, values] of Object.entries(tokens)) {
    const prefix = prefixes[group];
    if (prefix === undefined) continue;
    for (const [name, value] of Object.entries(values)) root.style.setProperty(`--${prefix}${name}`, value);
  }
}

export async function loadJsonDocument(path, fetchImplementation = fetch) {
  const response = await fetchImplementation(path);
  if (!response.ok) throw new Error(`Unable to load ${path}: HTTP ${response.status}`);
  return response.json();
}
