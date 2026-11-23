#!/usr/bin/env bash
# 用 collection 快照把旧 Qdrant 的数据搬到新 Qdrant（比如存储从 NAS 换到云盘时）。只需要 bash 和 curl。
#
# 旧库：每个 collection 建一个快照，下载到当前目录（<collection>.snapshot，留作备份）后删掉旧库上的
# 这份快照；不改旧库的数据。
# 新库：上传快照，按快照建出 collection（向量、payload、payload 索引都在）。新库里已经有同名
# collection 时跳过，免得后端切过去以后误跑一次、把新写入的数据覆盖掉；确定要覆盖就加 FORCE=1。
# 两边 Qdrant 的版本前两位（x.y）要相同：快照只能恢复到同一个小版本。
#
# 顺序：先跑完这个脚本，再在 Nacos 里把 QDRANT_CONFIG 改成新库并重启 API。反过来的话，后端启动时
# 发现新库里没有当前版本的 collection，会建一个空的并清掉它的写入台账，之后只能全部重新生成向量。
# 切过去以后打开前端「模型版本」页：出现「核对并补齐」或「补齐缺少的向量」就点一下，补上快照之后
# 入库的图片。从建快照到切换完成，先别入库、删图、改类别和场景。
#
# 用法（在两边都能连上、磁盘放得下快照的机器上，比如 API 服务器）：
#   OLD=http://旧地址:6333 OLD_KEY=旧key NEW=http://新地址:6333 NEW_KEY=新key bash migrate_qdrant_snapshots.sh
#   ... bash migrate_qdrant_snapshots.sh <collection> [<collection> ...]   只迁指定的；不写就迁旧库里的全部
# 哪边没开鉴权就不设那边的 KEY。
set -euo pipefail
: "${OLD:?要设置 OLD=http://旧Qdrant地址:6333}"
: "${NEW:?要设置 NEW=http://新Qdrant地址:6333}"
OLD_H="api-key: ${OLD_KEY:-}"  # 值为空时 curl 不发这个头
NEW_H="api-key: ${NEW_KEY:-}"

field() { grep -o "\"$1\":\"[^\"]*\"" | cut -d'"' -f4; }
version() { curl -sSf -H "$1" "$2/" | field version; }
points() { curl -sSf -H "$1" "$2/collections/$3" | grep -o '"points_count":[0-9]*' | cut -d: -f2; }

# 根路径不校验 API Key：先用要鉴权的接口确认两边都连得上、key 对
curl -sSf -o /dev/null -H "$OLD_H" "$OLD/collections" || { echo "连不上旧库，或者 OLD_KEY 不对" >&2; exit 1; }
curl -sSf -o /dev/null -H "$NEW_H" "$NEW/collections" || { echo "连不上新库，或者 NEW_KEY 不对" >&2; exit 1; }
old_version=$(version "$OLD_H" "$OLD")
new_version=$(version "$NEW_H" "$NEW")
echo "旧库 Qdrant $old_version，新库 Qdrant $new_version"
if [ "${old_version%.*}" != "${new_version%.*}" ]; then
  echo "版本前两位不同：快照只能恢复到同一个小版本的 Qdrant，先把新库换成 ${old_version%.*}.x" >&2
  exit 1
fi

for C in ${@:-$(curl -sSf -H "$OLD_H" "$OLD/collections" | field name)}; do
  echo "== $C"
  status=$(curl -s -o /dev/null -w '%{http_code}' -H "$NEW_H" "$NEW/collections/$C")
  if [ "$status" = 200 ] && [ "${FORCE:-0}" != 1 ]; then
    echo "   新库里已经有 $C，跳过（确定要用旧库的快照覆盖它就加 FORCE=1）"
    continue
  fi
  if [ "$status" != 200 ] && [ "$status" != 404 ]; then
    echo "   查询新库里的 $C 失败：HTTP $status" >&2
    exit 1
  fi
  snapshot=$(curl -sSf -X POST -H "$OLD_H" "$OLD/collections/$C/snapshots" | field name)
  curl -sSf -H "$OLD_H" -o "$C.snapshot" "$OLD/collections/$C/snapshots/$snapshot"
  curl -sSf -X DELETE -H "$OLD_H" "$OLD/collections/$C/snapshots/$snapshot" > /dev/null
  curl -sSf -X POST -H "$NEW_H" -F "snapshot=@$C.snapshot" \
    "$NEW/collections/$C/snapshots/upload?priority=snapshot" > /dev/null
  echo "   点数：旧 $(points "$OLD_H" "$OLD" "$C")，新 $(points "$NEW_H" "$NEW" "$C")"
done
