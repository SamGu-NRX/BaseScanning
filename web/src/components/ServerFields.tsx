// Server connection fields: where the placement server lives, and the optional bearer
// token for servers that loaded private rules. The token is an uncontrolled input: the
// store keeps it in memory for the next submission only, so nothing here can persist
// or log it, and no re-render ever needs to read it back.

interface ServerFieldsProps {
  readonly baseUrl: string;
  readonly hasToken: boolean;
  readonly onBaseUrlChange: (url: string) => void;
  readonly onTokenChange: (token: string) => void;
}

export function ServerFields({
  baseUrl,
  hasToken,
  onBaseUrlChange,
  onTokenChange,
}: ServerFieldsProps) {
  return (
    <fieldset>
      <legend>Server</legend>
      <p>
        <label htmlFor="server-base-url">Placement server</label>
        <br />
        <input
          id="server-base-url"
          type="url"
          size={40}
          spellCheck={false}
          value={baseUrl}
          onChange={(event) => onBaseUrlChange(event.target.value)}
        />
      </p>
      <p>
        <label htmlFor="server-token">Bearer token (optional)</label>
        <br />
        <input
          id="server-token"
          type="password"
          size={40}
          autoComplete="new-password"
          spellCheck={false}
          onChange={(event) => onTokenChange(event.target.value)}
        />
      </p>
      <p>
        {hasToken
          ? "A token is set. It stays in this page's memory only: never stored, never logged, gone when the page closes."
          : "Optional: only servers that loaded private rules need a bearer token. If set, it stays in this page's memory only."}
      </p>
    </fieldset>
  );
}
