# Freshservice

Connect HolmesGPT to [Freshservice](https://www.freshworks.com/freshservice/) (Freshworks) to read and write ITSM data: tickets, problems, changes, releases, requesters, agents, knowledge base articles, service catalog items and more. HolmesGPT can correlate incidents with recent changes to find root causes, and document its findings back on the relevant records.

## Prerequisites

- A Freshservice instance (e.g. `https://yourdomain.freshservice.com`)
- A Freshservice API key. To find it, log in to the agent portal and go to **Profile Settings**; the API key is shown on the right. The API key inherits the permissions of the agent it belongs to.

## Configuration

=== "Holmes CLI"

    Add the following to **~/.holmes/config.yaml**. Create the file if it doesn't exist:

    ```yaml
    toolsets:
      freshservice:
        enabled: true
        config:
          api_url: https://yourdomain.freshservice.com
          api_key: "{{ env.FRESHSERVICE_API_KEY }}"
    ```

    To test, run:

    ```bash
    holmes ask "list the urgent open tickets in freshservice"
    ```

=== "Robusta Helm Chart"

    ```yaml
    holmes:
      toolsets:
        freshservice:
          enabled: true
          config:
            api_url: https://yourdomain.freshservice.com
            api_key: "{{ env.FRESHSERVICE_API_KEY }}"
    ```

    Store the API key in a Kubernetes secret and expose it via `additionalEnvVars`:

    ```yaml
    holmes:
      additionalEnvVars:
        - name: FRESHSERVICE_API_KEY
          valueFrom:
            secretKeyRef:
              name: holmes-secrets
              key: freshservice-api-key
    ```

### Advanced Configuration

```yaml
toolsets:
  freshservice:
    enabled: true
    config:
      api_url: https://yourdomain.freshservice.com
      api_key: "{{ env.FRESHSERVICE_API_KEY }}"
      readonly: true         # disable tools that create or modify records (default: false)
      default_page_size: 30  # default number of records returned by list tools
```

By default the toolset can both read and write (create tickets, update records, add notes). Set `readonly: true` to restrict it to read operations only.

## Capabilities

| Tool Name | Description |
|-----------|-------------|
| freshservice_list_records | List records of any supported type: tickets, problems, changes, releases, requesters, agents, groups, departments, locations, assets, devices, contracts, knowledge base categories/folders/articles, service catalog items and more |
| freshservice_get_record | Fetch a single record by ID, optionally embedding related data (e.g. ticket stats or conversations) |
| freshservice_filter_tickets | Search tickets with the Freshservice filter query language (e.g. `priority:4 AND status:2`) |
| freshservice_get_ticket_conversations | Read the replies and private agent notes on a ticket |
| freshservice_search_solution_articles | Search knowledge base articles by keyword |
| freshservice_create_record | Create a record (e.g. open a ticket) |
| freshservice_update_record | Update fields on an existing record (e.g. reassign or resolve a ticket) |
| freshservice_add_note | Add a note to a ticket, problem, change or release |

Assets and devices are read and written through Freshservice's newer ITAM API (`/api/v2/itam/...`). Some classic record types (vendors, products, software) require Freshservice plans that include the classic CMDB; on other plans the API returns a clear error that HolmesGPT relays.

## Common Use Cases

```
Customers report checkout failures since Monday morning. Investigate the Freshservice tickets and find the root cause.
```

```
Summarize all urgent open tickets and who they are assigned to.
```

```
Which recent changes could have caused the payment incidents? Add your analysis as a note on the problem ticket.
```

```
Create a Freshservice ticket for the database connection errors we just found, and link the details.
```

## Demo Data

The repository ships with a seeding script that populates a Freshservice instance with a realistic demo dataset, including a "failure caused by a change" scenario for HolmesGPT to root-cause:

```bash
FRESHSERVICE_URL=https://yourdomain.freshservice.com \
FRESHSERVICE_API_KEY=xxx \
python scripts/seed_freshservice_demo.py
```
