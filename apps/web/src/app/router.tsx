import { createBrowserRouter } from "react-router";
import { ChatPage } from "../chat/ChatPage";
import { PrivacyPage } from "../privacy/PrivacyPage";
import { SimulatorPage } from "../simulator/SimulatorPage";
import { ErrorPage, NotFoundPage } from "./ErrorPage";

/**
 * Customer pages load eagerly; the agent dashboard is a separate chunk (ADR-0016), so customers
 * never download dashboard code. Only this file references `src/agent` (enforced by ESLint).
 */
export const router = createBrowserRouter([
  {
    errorElement: <ErrorPage />,
    children: [
      { path: "/", element: <ChatPage /> },
      { path: "/simulator", element: <SimulatorPage /> },
      { path: "/privacy", element: <PrivacyPage /> },
      { path: "/agent/*", lazy: async () => ({ Component: (await import("../agent/AgentApp")).AgentApp }) },
      { path: "*", element: <NotFoundPage /> },
    ],
  },
]);
