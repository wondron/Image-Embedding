import type { Scene, SceneCatalog } from '../../entities/scene'
import { fileTimestamp } from '../../shared/lib/saveFile'

export interface SceneConfigExportFile {
  filename: string
  content: string
  /** Scenes in the file. */
  count: number
}

/** One scene as GET /api/scenes states it (snake_case), so the file reads like the API. */
function exportScene(scene: Scene) {
  return {
    id: scene.id,
    code: scene.code,
    name: scene.name,
    description: scene.description,
    include_all: scene.includeAll,
    outputs_heating: scene.outputsHeating,
    is_active: scene.isActive,
    created_at: scene.createdAt,
    member_count: scene.memberCount,
    labels: scene.labels.map(label => ({
      id: label.id,
      code: label.code,
      name: label.name,
      sort_order: label.sortOrder,
      is_active: label.isActive,
      rule_count: label.ruleCount,
    })),
    rules: scene.rules.map(rule => ({
      id: rule.id,
      category: rule.category ? { id: rule.category.id, code: rule.category.code, name: rule.category.name } : null,
      cuisine: rule.cuisine,
      label: rule.label ? { id: rule.label.id, code: rule.label.code, name: rule.label.name } : null,
    })),
  }
}

/**
 * The whole scene configuration as a file: every scene (disabled ones too) with its labels and
 * rules. POST /api/scenes/import takes it back: scenes, labels and rules are matched by code (a
 * rule by its category's code or its cuisine), so ids, counts and timestamps are only for reading.
 */
export function buildSceneConfigExport(
  catalog: Pick<SceneCatalog, 'scenes' | 'defaultScene'>,
  exportedAt = new Date(),
): SceneConfigExportFile {
  const payload = {
    schema_version: 1,
    // What the file is: POST /api/scenes/import refuses a file of another kind
    kind: 'scene_config',
    exported_at: exportedAt.toISOString(),
    default_scene: catalog.defaultScene,
    scene_count: catalog.scenes.length,
    scenes: catalog.scenes.map(exportScene),
  }
  return {
    filename: `scene-config-${fileTimestamp(exportedAt)}.json`,
    content: `${JSON.stringify(payload, null, 2)}\n`,
    count: payload.scene_count,
  }
}
