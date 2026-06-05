# Trussflow Product Requirements

## Foreward

In the age of AI I am moving to a quicker iteration of design & development ideas. Trussflow is a project that I'm thinking of which is essentially a  Retrieval-Augmented Generation (RAG) graph for requirements so that an AI can turn those into implementable work packages for the entire project.

## May 31, 2026

The high-level requirements are strictly agnostic regarding technology and implementation. The objective of Trussflow is to utilize a top-level Vision document, encompassing the goals, user experience, and desires of the founders, to establish the project's actual requirements. It is intended that the input vision be unstructured—potentially a meeting transcript or similar format—which is then analyzed to create a requirements Knowledge Graph. This graph serves to identify gaps or ambiguities that may affect the ultimate user experience. Subsequently, an AI will highlight these deficiencies, propose potential solutions, and guide the founder through the resolution process.  
Requirements are uniquely identified by an ID, with relationships established through classifications such as "depends on," "is related to," or "is a child of." Each requirement must be unique and traceable through its history and various states, and all requirements must be persisted for future accessibility. Furthermore, requirements should adhere to a high standard, specifically following NASA's criteria for high-quality requirements.

Once product requirements and user experience expectations are finalized, the process moves to defining the solution. This second phase of requirements gathering involves evaluating the system architecture to determine which system best meets the top-level product requirements. This includes making decisions regarding technology stacks, architectural separation of concerns, and other technical choices, all while adhering to established system architecture principles to verify that the system is capable of fulfilling the product requirements. Iteration back to the product requirements may be necessary if certain requirements are found to be technically unimplementable, incompatible, or infeasible, which may result in product requirements being superseded.

Following the stabilization of product and system architecture requirements, the next phase involves elaborating on design requirements for various architectural sections. This level of design provides greater detail and fidelity regarding operational functionality. As design requirements are refined, there may be a need to iterate on system architecture or product requirements. The final stage aggregates implementation requirements for specific designs or entire sections for execution by implementers. Potential issues discovered during implementation may necessitate further changes to the requirements.

Throughout this process, all actions are traceable and recordable to ensure a comprehensive history of changes, origins, and rationales. This data enables an AI system to propose or execute requirement changes. Oversight will be provided by a separate AI agent, allowing for autonomous adjustments in cases where there is minimal or no impact on the overall product requirements.

While simultaneous multi-user support is not a requirement, the system must be capable of tracking requirements from various users and sources.

## How it is Supposed to Work

The core philosophy of Trussflow is that the system should not require manual specification of every detail. Instead, requirements are layered and hierarchical, allowing the user to reach a point where they are satisfied with the defined results without needing to oversee the specifics of design or implementation.  
A fundamental tenet is balancing user control with AI autonomy. This necessitates an associated value for each requirement that indicates the user's level of concern regarding its implementation or further elaboration. The system must distinguish between areas where the user demands high fidelity—such as security or specific user experience elements—and areas where the AI is free to operate within established constraints, such as UI layout details, API choices, or internal layering.  
The initial requirements process must identify which domains require further human elaboration and which provide the AI with the freedom to explore solutions independently.

## Environmental and Technical Top Level Requirements

The development and delivery of this product are governed by several environmental constraints. The system will be built within a software environment utilizing open-source, modern, and mature technologies that benefit from long-term support. Key priorities for the project include security, stability, and sustainability.  
Essential to the project is the ability to distribute the software freely; therefore, all open-source licenses and source materials must allow for unrestricted distribution by our team. Specific technical requirements include:

* The system must be distributable as a Docker container utilizing a Debian Python base image.  
* Development resources are centered on VS Code running on Ubuntu 24.04.  
* Primary AI targeting will focus on the GitHub Copilot.
* Neo4j as the requirements storage backend.
* LangGraph as the orchestration layer.
* Offload, prepare and validate using mechanical (non-AI) means for determninism and reduced token cost.
* Keep AI prompting and responses terse.
* Python 3.14 as the primary tool implementation language.
