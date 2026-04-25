export type Result<TValue, TError extends Error = Error> =
  | { readonly ok: true; readonly value: TValue }
  | { readonly ok: false; readonly error: TError };

export const ok = <TValue>(value: TValue): Result<TValue, never> => ({ ok: true, value });

export const err = <TError extends Error>(error: TError): Result<never, TError> => ({ ok: false, error });
