export interface SceneLabel { id: number; code: string; name: string; sortOrder: number; isActive: boolean; ruleCount: number }
export interface SceneRef { id: number; code: string; name: string | null }
export interface SceneRule { id: number; category: SceneRef | null; cuisine: string | null; label: SceneRef | null }
export interface Scene { id: number; code: string; name: string; description: string | null; includeAll: boolean; outputsHeating: boolean; isActive: boolean; createdAt: string | null; memberCount: number; labels: SceneLabel[]; rules: SceneRule[] }
export interface SceneCatalog { scenes: Scene[]; defaultScene: string; traceId: string | null }
export interface SceneInput { code: string; name: string; description: string | null; includeAll: boolean; outputsHeating: boolean; isActive: boolean }
export interface LabelInput { code: string; name: string; sortOrder: number; isActive: boolean }
/** Exactly one of categoryId / cuisine; labelId only with a category. */
export interface RuleInput { categoryId: number | null; cuisine: string | null; labelId: number | null }
/**
 * A configuration write. syncRequired: stored images follow only after POST /api/scenes/sync
 * (rules, a scene's isActive / includeAll); false when it already shows (labels, names, …).
 */
export interface SceneWrite { scene: Scene | null; deletedId: number | null; syncRequired: boolean; traceId: string | null }
export interface SyncBatch { processed: number; membershipChanged: number; payloadPushed: number; payloadMissing: string[]; nextAfter: string | null; done: boolean; traceId: string | null }
export interface Membership { sceneId: number; sceneCode: string; isActive: boolean; includeAll: boolean; isMember: boolean; origin: 'implicit' | 'rule' | 'manual' | null; manualOverride: boolean | null }
export interface MembershipSnapshot { imageId: string; payloadVersion: number; scenes: string[]; memberships: Membership[]; traceId: string | null }
/**
 * How a manual write reached the search index: pushed (search sees it now), missing (the image has
 * no vector in the current collection), not_ready (retrieval down), failed (logged on the server).
 * null: a server that does not push (older build).
 */
export type VectorSync = 'pushed' | 'missing' | 'not_ready' | 'failed'
export interface MembershipWrite extends MembershipSnapshot { syncRequired: boolean; vectorSync: VectorSync | null }
