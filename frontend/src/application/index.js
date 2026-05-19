import "../styles/index.css";

import { Application } from "@hotwired/stimulus";
import { definitionsFromContext } from "@hotwired/stimulus-webpack-helpers";

import Dropdown from '@stimulus-components/dropdown';
import RevealController from '@stimulus-components/reveal';

import { ANALYTICS_EVENT_TAXONOMY } from "../constants/analytics_events";

const application = Application.start();
application.analyticsEventTaxonomy = ANALYTICS_EVENT_TAXONOMY;

const context = require.context("../controllers", true, /\.js$/);
application.load(definitionsFromContext(context));

application.register('dropdown', Dropdown);
application.register('reveal', RevealController);
