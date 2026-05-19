import eventTaxonomy from "../../../core/analytics/event_taxonomy.json";

const canonicalEventNames = Object.keys(eventTaxonomy.events || {});

const analyticsEvents = canonicalEventNames.reduce((events, eventName) => {
  events[eventName.toUpperCase()] = eventName;
  return events;
}, {});

export const ANALYTICS_EVENT_TAXONOMY = Object.freeze(eventTaxonomy);
export const ANALYTICS_EVENT_NAMES = Object.freeze(canonicalEventNames);
export const ANALYTICS_EVENTS = Object.freeze(analyticsEvents);
export const DEPRECATED_ANALYTICS_EVENT_ALIASES = Object.freeze(
  eventTaxonomy.deprecated_aliases || {},
);
