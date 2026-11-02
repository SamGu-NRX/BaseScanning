# Smoke invocations (2026-10-10, obv/products-basescanning-hardening-20261009-r1)

```
cd server && uv run uvicorn api:app --host 127.0.0.1 --port 8123
```

## 1. bare JSON
```
curl -s -H "Content-Type: application/json" --data-binary @smoke/placement-request.json \
  -o smoke/placement-result.json -w "%{http_code}\n" http://127.0.0.1:8123/v1/placements
```

## 2. zip bundle
```
curl -s -F "bundle=@smoke/placement-request.zip" \
  -o smoke/placement-result-zip.json -w "%{http_code}\n" http://127.0.0.1:8123/v1/placements
```

## 3. bytes with no zip end record
```
curl -s -H "Content-Type: application/zip" --data-binary "not a zip at all" \
  -o smoke/unreadable-result.json -w "%{http_code}\n" http://127.0.0.1:8123/v1/placements
```
