export type Brand<TValue, TBrand extends string> = TValue & { readonly __brand: TBrand };

export const makeBrand = <TBrand extends string>(value: string, label: TBrand): Brand<string, TBrand> => {
  const normalized = value.trim();
  if (normalized.length === 0) {
    throw new Error(`${label} cannot be empty`);
  }
  return normalized as Brand<string, TBrand>;
};
