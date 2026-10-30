pragma ComponentBehavior: Bound

// EditorBase — common base of every dialog body (ParamForm and the visual editors). DialogSurface reads the contract
// below; concrete editors redefine requestApply() (validate, then emit apply(params)), reset() and undo() → bool
// (Ctrl+Z), and targetFiles() when some files must be left out of the job. The base versions apply {} / do nothing /
// run on every file.
//
//   required property var node     menu Node JSON of the action (SPEC §5.3): id, label, dialog, params, description
//   required property var files    FileInfo JSON objects (or plain paths / file:// URLs)
//   property var host              the DialogSurface (file pickers); null when used outside a dialog
//   signal apply(var params)       run the action with these params (DialogSurface starts the job and closes)
//   signal cancel()                close without running
//   function targetFiles() → var   the files (fileList entries) the job runs on, read when apply is emitted
//
//   Contract read by DialogSurface (editors set them):
//   property string applyLabel     label of the primary button ("" → the action label)
//   property string applyIcon      leading icon of the primary button
//   property bool canApply         primary button enabled
//   property bool busy             a preview or a pre-apply step is running (spinner on the primary button)
//   property bool dirty            the user changed something (a scrim click then does not discard the edits)
//   property bool resettable       show the Reset button
//   property bool showFooter       DialogSurface footer (Reset / hint / Apply)
//   property string footerHint     short status text in the footer ("1066 × 1600 px")
//   property bool footerHintError  footerHint is an error (error colour)
//   property bool wide             wants the large card (preview editors); false = compact form card
//
//   Helpers:
//   readonly property var fileList        normalised files (DialogUtils.normaliseFiles)
//   readonly property int fileCount
//   property int currentIndex             file shown by multi-file editors
//   readonly property var currentFile
//   readonly property var paramList       node.params as an array
//   function param(name) → Param JSON | null
//   function paramDefault(name, fallback)
//   function optionParams(produced) → names of node params the visual editor does not produce itself and that the
//                                     generic form can render (used for the embedded "Options" ParamForm exclude list)
//   function isVideo(file) / isPdf(file) / isAudio(file) / isImage(file)
//   function previewUrl(path) → file:// URL for Image.source ("" for empty paths)

import QtQuick
import "../services"
import "DialogUtils.js" as DU

FocusScope {
    id: root

    required property var node
    required property var files
    property var host: null

    property string applyLabel: ""
    property string applyIcon: "check"
    property bool canApply: true
    property bool busy: false
    property bool dirty: false
    property bool resettable: true
    property bool showFooter: true
    property string footerHint: ""
    property bool footerHintError: false
    property bool wide: true

    readonly property var fileList: DU.normaliseFiles(root.files)
    readonly property int fileCount: root.fileList.length
    property int currentIndex: 0
    readonly property var currentFile: (root.currentIndex >= 0 && root.currentIndex < root.fileList.length) ? root.fileList[root.currentIndex] : null
    readonly property var paramList: Array.isArray(root.node?.params) ? root.node.params.filter(p => p !== null && typeof p === "object" && typeof p.name === "string" && p.name !== "") : []

    // Param types the generic form renders; structured ones are produced by the visual editors.
    readonly property var formTypes: ["int", "float", "slider", "bool", "select", "text", "password", "color", "time", "timerange", "pages", "path", "dir", "textarea"]

    signal apply(var params)
    signal cancel

    // Default implementations; concrete editors redefine them.
    function requestApply(): void {
        if (root.canApply && !root.busy)
            root.apply({});
    }

    function reset(): void {
    }

    function undo(): bool {
        return false;
    }

    function targetFiles(): var {
        return root.fileList;
    }

    function param(name: string): var {
        for (const p of root.paramList) {
            if (p.name === name)
                return p;
        }
        return null;
    }

    function paramDefault(name: string, fallback: var): var {
        const p = root.param(name);
        if (p === null || p.default === undefined || p.default === null)
            return fallback;
        return p.default;
    }

    // Names to exclude from the embedded options form: everything the editor produces plus structured types.
    function optionExcludes(produced: var): var {
        const out = Array.isArray(produced) ? produced.slice() : [];
        for (const p of root.paramList) {
            if (root.formTypes.indexOf(String(p.type ?? "")) === -1 && out.indexOf(p.name) === -1)
                out.push(p.name);
        }
        return out;
    }

    function optionParams(produced: var): var {
        const ex = root.optionExcludes(produced);
        return root.paramList.filter(p => ex.indexOf(p.name) === -1).map(p => p.name);
    }

    function isVideo(file: var): bool {
        return (file?.category ?? "") === "video";
    }

    function isAudio(file: var): bool {
        return (file?.category ?? "") === "audio";
    }

    function isPdf(file: var): bool {
        return (file?.category ?? "") === "pdf" || (file?.ext ?? "") === "pdf";
    }

    function isImage(file: var): bool {
        const c = file?.category ?? "";
        return c === "image" || c === "svg";
    }

    function previewUrl(path: var): string {
        return (typeof path === "string" && path !== "") ? Backend.fileUrl(path) : "";
    }

    implicitWidth: root.wide ? Theme.s(1040) : Theme.s(460)
    implicitHeight: root.wide ? Theme.s(660) : Theme.s(320)
    focus: true

    onCurrentIndexChanged: {
        if (root.currentIndex >= root.fileList.length && root.fileList.length > 0)
            root.currentIndex = root.fileList.length - 1;
        else if (root.currentIndex < 0)
            root.currentIndex = 0;
    }
}
