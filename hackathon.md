# MongoDB Persistent Context Sprint Hackathon 2026 **Participant Guide**

Welcome to [The Persistent Context Sprint Hackathon](https://cerebralvalley.ai/e/persistent-context-sprint-hackathon)! 👋 We’re thrilled to have you on board. This guide is your all-in-one resource for the event, including schedule, rules, technical resources, problem statements, judging information, and more. Please read this carefully; most answers can be found here.

## **1️⃣ Your Goal**

**No Cold Start**

Every agent starts from nothing. Build one that doesn't. Use MongoDB to hold state, memory, and live application data so your agent comes back with what it learned last time instead of relearning everything. What you store, retrieve, and checkpoint should change what the system does next, not just fill the prompt.

Example Projects:

- An agent that tracks which retrieval strategies actually worked (chunk size, reranking, source weights, k values) and adapts future runs based on outcome scores stored in MongoDB.
- A coding agent that keeps repo conventions and past fixes in Atlas, retrieves them via vector search on new tasks, and checkpoints through LangGraph so a crash mid-refactor doesn't lose progress
- A multi-agent system that shares context through MongoDB, discovers capabilities via vector search, and coordinates through change streams so handoffs carry state forward

We recommend familiarizing yourself with MongoDB's [MCP Server](https://www.mongodb.com/products/tools/mcp-server) and [Agent Skills](https://github.com/mongodb/agent-skills) to start building quickly.

The **Best Project Built with ElevenLabs** prize will be awarded based off the following criteria:

- **Agentic Depth:** Does the project go beyond simple text-to-speech? We prioritize autonomous agents that handle complex logic and real-time dialogue.

- **Interaction Design:** How lifelike is the experience? We value projects that master low-latency response times and emotional inflection.

- **Technical Integration:** Creative use of the ElevenLabs API - especially multimodal implementations (Voice + Video) or clever prompt engineering for the Agent’s personality.

- **Novelty:** A use case we haven’t seen before that solves a real-world problem using conversational AI.

## **2️⃣ Getting Ready – Location & Arrival**

Location: Pier 48, San Francisco, CA, 94158

Note: The hackathon will be held on the **Embarcadero** **Stage**.

[googleMap]

### **Arrival Instructions**

[.local Build Fest](https://www.mongodb.com/events/mongodb-local/build-fest?utm_source=luma) doors open at 8:30 AM PT and hackathon check-in opens at 1:00 PM PT. Make sure to bring a government-issued photo ID.  Enter at Terry A Francois Blvd, where you’ll be directed to registration and then proceed to security and entrance to the event.

Build Fest is where the hackathon is hosted, and your entry is already complimentary as a hackathon participant. MongoDB will email you directly with your QR code and day-of details for check-in. Please keep your eyes peeled for this, as you need to access Build Fest to access the hackathon.

**Getting Here**

**Public Transit (recommended):** Pier 48 is one block from the Mission Rock Muni station, served by the T Third/Central Subway line. The 4th & King Caltrain station is approximately a 15-minute walk. Visitors arriving by BART can transfer to the T Third line at Powell Street Station or connect to nearby Muni service.

**Parking:** Parking near Pier 48 is limited and may be especially difficult during events at Oracle Park or Chase Center. Paid parking may be available in nearby Giants lots and private garages, but availability and rates vary. Rideshare drop-off should take place near the designated event entrance along Terry A. Francois Boulevard or Mission Rock Street. 

*Parking is limited — we strongly recommend taking public transit or rideshare.*

## **3️⃣ Connect with the Community**

Join the Hackathon Discord to meet other participants, get official updates, begin forming teams: <https://discord.gg/8VUq28JrP2>

## **4️⃣ Schedule Overview**

- **1:00PM – 1:30PM:** Registration, Team formation, Opening remarks

- **1:30PM:** Hackathon Begins

- **5:00PM:** Submissions Due

- **5:15PM – 6:30PM:** First Round Judging

- **6:30PM - 7:30 PM:** Finalist Announcements, On-Stage Demos, Live Voting

- **7:30PM:** Community Vote and Winners Announced 

- **8:00PM:** Loud Luxury Performs 

## **5️⃣ Hackathon Rules**

- **Open Source:** Project repositories **must be public**.

- **Team Size:** A **maximum of four** team members per team. Solo participants are allowed.

- **Demo Requirements:** Your demo **must only highlight the specific features, code, and functionality that your team built during the hackathon**. Judges must be able to clearly identify what was created during the event. Failure to clearly identify your original contributions will result in immediate disqualification.

- **New Work Only:** You may not present an existing project as your own work. Failure to clearly distinguish your contributions will result in immediate disqualification.

- **Banned Projects:** Projects will be **disqualified** if they: violate legal, ethical, or platform policies, use code, data, or assets you do not have the rights to.

### **🚫 Sample Anti-Projects to NOT DO — STRICTLY NO:**

- AI Mental Health Advisor (*note: this only applies to basic chatbots with limited technical complexity)*

- Basic RAG Applications

- Streamlit Applications

- Image Analyzers (*note: this only applies to basic projects with limited technical complexity)*

- “AI for Education” Chatbot

- AI Job Application Screener

- AI Nutrition Coach (*note: this only applies to basic chatbots with limited technical complexity)*

- Personality Analyzers

- Any project where a dashboard is the main feature

- Sports analyzers or coaches

## **6️⃣ MongoDB Provided Resources**

MongoDB has provided the following resources to guide you during the hackathon:

This hackathon focuses on building AI applications with agent memory: systems that retain context over time and take action on real data, including databases, documents, code, and other business systems.

### Recommended Starting Point

Participants are encouraged to configure the following three resources before beginning development. Together, they provide an AI coding assistant with the context, connectivity, and prompting guidance needed to work effectively with MongoDB.

- [**MongoDB Agent Skills**](https://www.mongodb.com/docs/agent-skills/) — A set of instructions that can be installed into an AI coding assistant (such as Claude, Cursor, or GitHub Copilot) to provide it with MongoDB best practices for connecting to a database, structuring data, writing queries, and implementing search.

- [**MongoDB MCP Server**](https://www.mongodb.com/docs/mcp-server/get-started/) — Enables an AI coding assistant to connect directly to a live MongoDB database to inspect data, run queries, retrieve counts, and manage Atlas resources.

- [**Natural Language to MongoDB Queries**](https://www.mongodb.com/docs/manual/natural-language-to-mongodb/) — Guidance on how to prompt an AI assistant effectively, including what details to provide about the desired task, data structure, and output format.

**Recommended sequence:** install Agent Skills, connect the MCP Server, and reference the prompting guide throughout development.

### Data and Search

- [**Sample Movie Dataset**](https://www.mongodb.com/docs/atlas/sample-data/sample-mflix) — A ready-to-use dataset that can be loaded in minutes and is preconfigured for AI-powered search.

- [**Data Modeling in MongoDB**](https://www.mongodb.com/docs/manual/data-modeling/#data-modeling) — Best practices for structuring data for efficient use by applications and AI agents.

- [**Vector Search**](https://www.mongodb.com/products/platform/atlas-vector-search) — Enables search based on semantic meaning rather than exact keyword matches, forming the basis for long-term, retrievable agent memory.

- [**Atlas Search**](https://www.mongodb.com/docs/atlas/atlas-search/) — Provides fast, typo-tolerant keyword search capabilities.

- [**Automated Embeddings**](https://www.mongodb.com/company/blog/product-release-announcements/unlocking-ai-search-introducing-automated-embedding-in-mongodb-vector-search) — Generates and maintains vector embeddings within the database, removing the need for a separate embedding pipeline.

- [**Embedding and Reranking API**](https://www.mongodb.com/docs/api/doc/atlas-embedding-and-reranking-api/) — A single endpoint for embedding and reranking operations, accessible from any application or technology stack.

### Memory and Agents

- [**Building an Agent with Memory and Function Calling**](https://www.mongodb.com/developer/products/atlas/interactive-rag-mongodb-atlas-function-calling-api/) — An example of an agent that retains context and executes functions to act on data, closely aligned with the hackathon's theme.

- [**Adding Memory to a Chat Application (Python)**](https://www.mongodb.com/developer/products/atlas/advanced-rag-langchain-mongodb/) — Demonstrates how to maintain context across a conversation using LangChain and MongoDB Atlas.

- [**Adding Memory to a Chat Application (JavaScript)**](https://www.mongodb.com/developer/products/atlas/add-memory-to-javascript-rag-application-mongodb-langchain/) — The same approach implemented in JavaScript.

### State

[**Build an AI Agent with LangGraph and MongoDB Atlas**](https://www.mongodb.com/docs/atlas/ai-integrations/langgraph/build-agents/) — A practical example using thread-specific checkpoints and conversation persistence.

[**State & Persistence: The Problem of Agent Reliability**](https://www.mongodb.com/company/blog/technical/state-persistence-the-problem-of-agent-reliability) — A deeper look at checkpoints, suspend/resume, crash recovery, and where state ends and memory begins.

### Context

[**Build AI Agents with MongoDB**](https://www.mongodb.com/docs/atlas/ai-agents/) — Agents that use MongoDB for retrieval alongside APIs and external tools.

[**GraphRAG with MongoDB and LangChain**](https://www.mongodb.com/docs/atlas/ai-integrations/langchain/graph-rag/) — Ingest external documents and retrieve relationship-aware context.

### Starter Code

- [**GenAI Showcase**](https://github.com/mongodb-developer/GenAI-Showcase/tree/main) — A comprehensive library of example applications spanning multiple AI frameworks and programming languages.

- [**MongoDB and Python Quickstart**](https://github.com/mongodb-developer/mongodb-atlas-python-quickstart/blob/main/quickstart-1-getting-started-atlas-python.ipynb) — An introductory guide for building with MongoDB Atlas and Python.

- [**MERN Starter Application**](https://github.com/mongodb-developer/mern-stack-example) — A starter project using MongoDB, Express, React, and Node.js.

Participants who are uncertain where to begin should complete the three items under Recommended Starting Point, then consult the GenAI Showcase for relevant examples.

## **7️⃣  Partner Provided Resources**

### Cursor

Coming soon!

### ElevenLabs

-  ElevenLabs is providing 1 month free of their Creator tier (normally $22/month, 131k credits)

Participants can claim their free ElevenLabs access through the automated Discord system:

1. Join the Discord server:[ https://discord.com/invite/VnBvbbcdEC](https://discord.com/invite/VnBvbbcdEC)

2. Gain access to the #🎟️│coupon-codes channel

3. Click "Start Redemption"

4. Select the event and fill out the form using the email used for registration

5. The bot sends the unique coupon code

Video Tutorial:[ https://youtu.be/S143_JtCtV8](https://youtu.be/S143_JtCtV8)

### Fireworks

Coming soon!

### LangChain

- LangChain is providing $50 in credits and deployments access for your use in the hackathon! Please view instructions to claim and use them [here](https://app.notion.com/p/Hackathon-Resources-from-LangChain-34f808527b1780c8a82bd0b8f0c322a2).  

### OpenRouter

- OpenRouter is providing $10 in API credits for your use in the hackathon! Please view instructions to claim and use them [here](https://app.notion.com/p/openrouter/OpenRouter-x-MongoDB-3b52fd57c4dc80a4bf07ed6ab238aa2b). 

## **8️⃣ Submission Process**

Teams should submit [**here**](https://cerebralvalley.ai/e/persistent-context-sprint-hackathon/submit) when they have completed hacking. In the submission form, **you will have to submit a short one minute demo video**. This should be a video highlighting the specific features, code, and functionality that your team built during the hackathon.

[linkEmbed]

Please double check that your repository is public, your demo link is accessible, and all team members have been added to the submission page.

Check your inbox for an email with a link to your **Atlas Hackathon Sandbox**, and create your project + cluster through it. **Your hackathon build must live in this sandbox to be eligible for the finalist round**.

## **9️⃣ Judging Process**

Judging will take place in two rounds.

**Round One:** Judging happens asynchronously. Every team submits a short demo video and a link to their public repo, and judges review submissions on their own time and score them against the criteria below:

> **Impact Potential (20%)**: What is the project’s long-term potential for success? Will this project have a long-lasting impact on the industry, world, or any other areas? How useful and substantial is this project beyond the scope of the hackathon?
> ****Demo (30%)**: How well has the team implemented their core idea? Does it work well live? How is it presented?
> ****Creativity & Originality (35%)**: Has this concept been seen before? In what ways does this project differentiate itself, and what innovations does it bring to its respective field? Does it tackle the problem statements in a unique way?