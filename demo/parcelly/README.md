# Parcelly

Shipment API of the Parcelly courier (Node.js, Express, Ajv). Data is kept in memory.

```
npm install
npm start
npm test
```

| Variable  | Default   | Meaning                                                       |
| --------- | --------- | ------------------------------------------------------------- |
| `PORT`    | `8080`    | HTTP port.                                                    |
| `HOST`    | `0.0.0.0` | Bind address.                                                 |
| `SANDBOX` | unset     | `1` enables the partner sandbox endpoints under `/__sandbox`. |

Partner-facing documentation: [public-docs/API.md](public-docs/API.md). Internal rules: [internal-wiki/](internal-wiki/).
