# Interactive first-launch logo

Approved by Willem on 2026-09-05: a matte, tactile 3D sculpture using the existing slate ensō, terracotta Buddy and gold glasses. Proceed to implementation without further design questions.

- Show the complete sculpture on the signed-out landing and onboarding welcome. Keep authentication and onboarding actions immediately available.
- Give the ring and Buddy actual geometry, soft directional lighting, visible edges and a warm floating shadow. Use local procedural RealityKit geometry; no downloaded models or services.
- Introduce the sculpture at an angle, then settle into gentle motion. Dragging rotates it in two axes and moves it slightly within its stage. A flick adds bounded angular momentum; damping and a spring return it to center and a readable orientation.
- Keep the welcome interactive until Continue. On continuation, transition to the small existing Buddy guide. Resumed onboarding starts with the small guide.
- Reduce Motion uses the settled static brand image and no inertial or ambient animation. Suspend the animation driver when inactive and destroy it on leaving the screen.
- Preserve light/dark surfaces, accessibility labels, Dynamic Type and the existing sign-in/debug entry points. The logo must never intercept controls outside its stage.

Validation: build current checkout, exercise both screens on iPhone, drag and release, verify continuation, light/dark, large text and Reduce Motion. Record screenshots and focused motion tests. Physical-device frame pacing remains separate from Simulator evidence.
