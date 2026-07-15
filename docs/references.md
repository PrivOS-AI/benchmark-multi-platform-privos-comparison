# References & evidence basis

The payload shapes in `fixtures/` are modeled on these public API specifications.
They ground the claim that the fixtures are *representative*, not invented.

## Telegram Bot API
- `getUpdates` returns an array of `Update` objects; each `Message` carries
  `message_id`, `from` (`User`), `chat` (`Chat`), `date`, `text`.
  https://core.telegram.org/bots/api#getupdates
- `sendMessage` request fields (`chat_id`, `text`, `parse_mode`, …) and the
  returned `Message`. https://core.telegram.org/bots/api#sendmessage
- Object model (`User`, `Chat`, `Message`): https://core.telegram.org/bots/api#available-types

## Notion API
- Create a page — request `parent` + `properties` with type-wrapped values;
  response is a full `Page` object.
  https://developers.notion.com/reference/post-page
- Retrieve a database — `Database` object with the full `properties` schema
  (per-property type config, `status`/`select` option lists and groups).
  https://developers.notion.com/reference/retrieve-a-database
- Query a database — paginated list of full `Page` objects.
  https://developers.notion.com/reference/post-database-query
- Property value / object shapes (`title`, `status`, `select`, `date`, `people`,
  `rich_text`, `annotations`):
  https://developers.notion.com/reference/property-value-object

## HubSpot CRM API (v3)
- Update object (PATCH) — returns the object with default + updated properties.
  https://developers.hubspot.com/docs/api/crm/contacts
- Search — `filterGroups` (OR of ANDs), `properties`, response `results[]` with
  `properties`, `createdAt`, `updatedAt`, `archived`.
  https://developers.hubspot.com/docs/api/crm/search
- Deals object + default properties (`dealstage`, `amount`, `pipeline`,
  `closedate`, `hs_lastmodifieddate`): https://developers.hubspot.com/docs/api/crm/deals

## Discord API (v10)
- Get channel messages — array of `Message` objects (`id`, `author`, `content`,
  `attachments`, `embeds`, `mentions`, `timestamp`, …).
  https://discord.com/developers/docs/resources/channel#get-channel-messages
- Create message — request fields + returned `Message`.
  https://discord.com/developers/docs/resources/channel#create-message
- Message / User objects: https://discord.com/developers/docs/resources/channel#message-object

## Trello REST API
- Create a card — `idList` + name/desc/due; returns full `Card` object (badges,
  cover, shortUrl, board/list ids).
  https://developer.atlassian.com/cloud/trello/rest/api-group-cards/#api-cards-post
- Get cards in a list — array of `Card` objects.
  https://developer.atlassian.com/cloud/trello/rest/api-group-lists/#api-lists-id-cards-get
- Card object fields: https://developer.atlassian.com/cloud/trello/rest/api-group-cards/#api-cards-id-get

## PrivOS unified surface
- Hub REST `/api/v1` (rooms/messages, lists/items). The MCP-app REST-first
  integration pattern and unified room + list-item data model are documented in
  the PrivOS codebase (`privos-hub`) and project notes. Message and list-item
  shapes here mirror hub responses (`_id`, `rid`, `u`, `ts`, `msg` for messages;
  `_id`, `listId`, `stageId`, `fields` for list items).

## Tokenizer
- `tiktoken` (`cl100k_base` BPE encoding). https://github.com/openai/tiktoken
  Used as a stable, reproducible proxy; ratios are within-tokenizer and robust to
  the specific BPE model.

## Notes on fidelity
- Tool counts (Telegram ~9, Notion ~15, HubSpot ~25) are approximate catalog
  sizes for typical MCP servers exposing these APIs; parameterized in
  `tools.json`.
- Real HubSpot/Notion responses grow with configured custom properties/fields,
  which would increase — never decrease — the multi-platform side.
