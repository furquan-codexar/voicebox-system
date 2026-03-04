import { AnimatePresence, motion } from 'framer-motion';
import {
  AlertCircle,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  Download,
  FileAudio,
  FileJson,
  FileText,
  Loader2,
  Play,
  Upload,
  Youtube,
} from 'lucide-react';
import { useRef, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Progress } from '@/components/ui/progress';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Textarea } from '@/components/ui/textarea';
import { useToast } from '@/components/ui/use-toast';
import { LANGUAGE_OPTIONS } from '@/lib/constants/languages';
import { useBatchClone } from '@/lib/hooks/useBatchClone';
import { cn } from '@/lib/utils/cn';

const STT_MODELS = [
  { value: 'tiny', label: 'Tiny' },
  { value: 'base', label: 'Base' },
  { value: 'small', label: 'Small' },
  { value: 'medium', label: 'Medium' },
  { value: 'large', label: 'Large' },
];

const AUDIO_EXTENSIONS = '.wav,.mp3,.flac,.ogg,.m4a';
const CARD_CLASS =
  'rounded-2xl border border-accent/20 bg-card shadow-sm overflow-hidden';

const EXAMPLE_JSON = `{
  "categories": {
    "greetings": {
      "wavs": [
        { "wav": "hello.wav", "text": "hello" },
        { "wav": "yes.wav", "text": "yes" },
        { "wav": "okay.wav", "text": "okay" }
      ]
    }
  }
}`;

function preventDefault(e: React.DragEvent) {
  e.preventDefault();
  e.stopPropagation();
}

export function BulkCloneTab() {
  const { toast } = useToast();
  const {
    startBatchClone,
    isStarting,
    batchId,
    status,
    progress,
    error,
    downloadZipUrl,
    reset,
  } = useBatchClone();

  const [mode, setMode] = useState<'youtube' | 'upload'>('youtube');
  const [youtubeUrl, setYoutubeUrl] = useState('');
  const [startTime, setStartTime] = useState('');
  const [endTime, setEndTime] = useState('');
  const [textLines, setTextLines] = useState('');
  const [language, setLanguage] = useState('en');
  const [sttModel, setSttModel] = useState('base');
  const [audioFileCount, setAudioFileCount] = useState(0);
  const [audioFiles, setAudioFiles] = useState<File[]>([]);
  const [hasTextFile, setHasTextFile] = useState(false);
  const [textFileName, setTextFileName] = useState<string | null>(null);
  const [textInputMode, setTextInputMode] = useState<'type' | 'txt' | 'json'>('type');
  const [hasJsonFile, setHasJsonFile] = useState(false);
  const [jsonFile, setJsonFile] = useState<File | null>(null);
  const [outputZipName, setOutputZipName] = useState('');
  const [audioDropActive, setAudioDropActive] = useState(false);
  const [txtDropActive, setTxtDropActive] = useState(false);
  const [jsonSchemaOpen, setJsonSchemaOpen] = useState(false);

  const textFileRef = useRef<HTMLInputElement>(null);
  const jsonFileRef = useRef<HTMLInputElement>(null);
  const audioFilesRef = useRef<HTMLInputElement>(null);

  const handleRun = async () => {
    if (textInputMode === 'json') {
      if (!jsonFile || !hasJsonFile) {
        toast({
          title: 'JSON file required',
          description: 'Upload a JSON file matching the schema.',
          variant: 'destructive',
        });
        return;
      }
    } else {
      const lines = textLines
        .trim()
        .split('\n')
        .map((l) => l.trim())
        .filter(Boolean);
      if (lines.length === 0) {
        toast({
          title: 'Text required',
          description: 'Enter at least one phrase per line, or upload a .txt or .json file.',
          variant: 'destructive',
        });
        return;
      }
    }

    const formData = new FormData();
    formData.append('mode', mode);
    formData.append('language', language);
    formData.append('stt_model', sttModel);
    formData.append('text_input_mode', textInputMode);
    if (outputZipName.trim()) {
      formData.append('output_zip_name', outputZipName.trim());
    }

    if (textInputMode === 'json' && jsonFile && hasJsonFile) {
      formData.append('json_file', jsonFile);
    } else if (textInputMode === 'txt' && textFileRef.current?.files?.[0] && hasTextFile) {
      formData.append('text_file', textFileRef.current.files[0]);
    } else {
      const lines = textLines
        .trim()
        .split('\n')
        .map((l) => l.trim())
        .filter(Boolean);
      formData.append('text', lines.join('\n'));
    }

    if (mode === 'youtube') {
      if (!youtubeUrl.trim()) {
        toast({
          title: 'YouTube URL required',
          description: 'Paste the YouTube video URL.',
          variant: 'destructive',
        });
        return;
      }
      if (!startTime.trim() || !endTime.trim()) {
        toast({
          title: 'Timestamps required',
          description: 'Enter start and end times (e.g. 0 and 30, or 1:30).',
          variant: 'destructive',
        });
        return;
      }
      formData.append('youtube_url', youtubeUrl.trim());
      formData.append('start_seconds', startTime.trim());
      formData.append('end_seconds', endTime.trim());
    } else {
      const files = audioFilesRef.current?.files;
      if (!files || files.length === 0) {
        toast({
          title: 'Audio files required',
          description: 'Upload at least one audio file.',
          variant: 'destructive',
        });
        return;
      }
      for (let i = 0; i < files.length; i++) {
        formData.append('audio_files', files[i]);
      }
    }

    try {
      await startBatchClone(formData);
      toast({
        title: 'Batch started',
        description: 'Processing in progress. You can monitor progress below.',
      });
    } catch (err) {
      toast({
        title: 'Failed to start batch',
        description: err instanceof Error ? err.message : 'Unknown error',
        variant: 'destructive',
      });
    }
  };

  const handleTextFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    setHasTextFile(!!file);
    setTextFileName(file?.name ?? null);
    if (file) {
      const reader = new FileReader();
      reader.onload = () => {
        setTextLines((reader.result as string) || '');
      };
      reader.readAsText(file);
    }
  };

  const handleJsonFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0] ?? null;
    setHasJsonFile(!!file);
    setJsonFile(file);
  };

  const handleAudioFiles = (files: FileList | null) => {
    if (!files?.length) {
      setAudioFileCount(0);
      setAudioFiles([]);
      return;
    }
    const list = Array.from(files);
    setAudioFiles(list);
    setAudioFileCount(list.length);
    if (audioFilesRef.current) {
      const dt = new DataTransfer();
      list.forEach((f) => dt.items.add(f));
      audioFilesRef.current.files = dt.files;
    }
  };

  const handleAudioDrop = (e: React.DragEvent) => {
    preventDefault(e);
    setAudioDropActive(false);
    const files = e.dataTransfer.files;
    if (files?.length) handleAudioFiles(files);
  };

  const handleTxtDrop = (e: React.DragEvent) => {
    preventDefault(e);
    setTxtDropActive(false);
    const file = e.dataTransfer.files?.[0];
    if (file?.name.toLowerCase().endsWith('.txt')) {
      const input = textFileRef.current;
      if (input) {
        const dt = new DataTransfer();
        dt.items.add(file);
        input.files = dt.files;
        handleTextFileChange({ target: input } as React.ChangeEvent<HTMLInputElement>);
      }
    }
  };

  const handleDownloadZip = async () => {
    if (!downloadZipUrl) return;
    try {
      const res = await fetch(downloadZipUrl);
      if (!res.ok) throw new Error(res.statusText);
      const blob = await res.blob();
      let filename = 'voice-clone-batch.zip';
      const disp = res.headers.get('Content-Disposition');
      if (disp) {
        const match = disp.match(/filename\*?=(?:UTF-8'')?["']?([^"'\s;]+)["']?/i) ?? disp.match(/filename=["']?([^"'\s;]+)["']?/i);
        if (match?.[1]) filename = decodeURIComponent(match[1].trim());
      }
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      toast({
        title: 'Download failed',
        description: e instanceof Error ? e.message : 'Could not download ZIP',
        variant: 'destructive',
      });
    }
  };

  const lineCount = textLines
    .trim()
    .split('\n')
    .map((l) => l.trim())
    .filter(Boolean).length;
  const hasText =
    textInputMode === 'json'
      ? hasJsonFile
      : lineCount > 0 || hasTextFile;
  const canRun =
    !isStarting &&
    !batchId &&
    ((mode === 'youtube' && youtubeUrl.trim() && startTime.trim() && endTime.trim()) ||
      (mode === 'upload' && audioFileCount > 0)) &&
    hasText;

  const totalSteps = (progress?.total_sources ?? 1) * (progress?.total_lines ?? 1) || 1;
  const doneSteps =
    ((progress?.current_source ?? 1) - 1) * (progress?.total_lines ?? 0) +
    (progress?.current_line ?? 0);
  const progressPercent = totalSteps ? Math.min(100, (doneSteps / totalSteps) * 100) : 0;

  return (
    <div className="flex flex-col h-full overflow-auto">
      <div className="py-6 px-4 max-w-4xl mx-auto w-full space-y-8">
        <motion.header
          initial={{ opacity: 0, y: -8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.3 }}
          className="space-y-1"
        >
          <h1 className="text-2xl font-semibold tracking-tight">Bulk Voice Clone</h1>
          <p className="text-muted-foreground text-sm">
            Clone voices from YouTube or uploaded audio, then generate speech for multiple
            phrases. Each line produces a separate output file. A ZIP is provided when
            complete.
          </p>
        </motion.header>

        <motion.div
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.3, delay: 0.05 }}
          className={cn(CARD_CLASS, 'p-5')}
        >
          <Label className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
            Source
          </Label>
          <Tabs
            value={mode}
            onValueChange={(v) => setMode(v as 'youtube' | 'upload')}
            className="mt-3"
          >
            <TabsList className="grid w-full grid-cols-2 h-11 rounded-full bg-muted p-1">
              <TabsTrigger
                value="youtube"
                className="rounded-full data-[state=active]:bg-background data-[state=active]:shadow-sm gap-2"
              >
                <Youtube className="h-4 w-4" />
                From YouTube
              </TabsTrigger>
              <TabsTrigger
                value="upload"
                className="rounded-full data-[state=active]:bg-background data-[state=active]:shadow-sm gap-2"
              >
                <Upload className="h-4 w-4" />
                From Upload
              </TabsTrigger>
            </TabsList>

            <TabsContent value="youtube" className="mt-4 space-y-4">
              <div className="space-y-2">
                <Label htmlFor="youtube-url">YouTube URL</Label>
                <Input
                  id="youtube-url"
                  placeholder="https://www.youtube.com/watch?v=..."
                  value={youtubeUrl}
                  onChange={(e) => setYoutubeUrl(e.target.value)}
                  className="rounded-xl"
                />
              </div>
              <div>
                <Label className="text-muted-foreground text-xs">Clip range</Label>
                <div className="grid grid-cols-2 gap-3 mt-2">
                  <div className="space-y-1">
                    <Label htmlFor="start-time" className="text-xs">
                      Start (seconds or M:SS)
                    </Label>
                    <Input
                      id="start-time"
                      placeholder="0 or 1:30"
                      value={startTime}
                      onChange={(e) => setStartTime(e.target.value)}
                      className="rounded-xl"
                    />
                  </div>
                  <div className="space-y-1">
                    <Label htmlFor="end-time" className="text-xs">
                      End (seconds or M:SS)
                    </Label>
                    <Input
                      id="end-time"
                      placeholder="30 or 2:00"
                      value={endTime}
                      onChange={(e) => setEndTime(e.target.value)}
                      className="rounded-xl"
                    />
                  </div>
                </div>
              </div>
            </TabsContent>

            <TabsContent value="upload" className="mt-4">
              <div
                role="button"
                tabIndex={0}
                onDragOver={(e) => {
                  preventDefault(e);
                  setAudioDropActive(true);
                }}
                onDragLeave={(e) => {
                  preventDefault(e);
                  setAudioDropActive(false);
                }}
                onDrop={handleAudioDrop}
                onClick={() => audioFilesRef.current?.click()}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    audioFilesRef.current?.click();
                  }
                }}
                className={cn(
                  'border-2 border-dashed rounded-xl p-6 text-center transition-colors cursor-pointer',
                  audioDropActive
                    ? 'border-accent bg-accent/10 text-accent-foreground'
                    : 'border-muted-foreground/30 hover:border-muted-foreground/50 hover:bg-muted/30',
                )}
              >
                <input
                  ref={audioFilesRef}
                  type="file"
                  accept={AUDIO_EXTENSIONS}
                  multiple
                  className="sr-only"
                  onChange={(e) => handleAudioFiles(e.target.files)}
                />
                {audioFileCount > 0 ? (
                  <div className="space-y-1">
                    <FileAudio className="h-10 w-10 mx-auto text-accent" />
                    <p className="font-medium text-sm">
                      {audioFileCount} file{audioFileCount !== 1 ? 's' : ''} selected
                    </p>
                    {audioFiles.length <= 5 &&
                      audioFiles.map((f) => (
                        <p key={f.name} className="text-xs text-muted-foreground truncate">
                          {f.name}
                        </p>
                      ))}
                    {audioFiles.length > 5 && (
                      <p className="text-xs text-muted-foreground">
                        +{audioFiles.length - 5} more
                      </p>
                    )}
                    <p className="text-xs text-muted-foreground mt-2">
                      Click or drop to change
                    </p>
                  </div>
                ) : (
                  <>
                    <Upload className="h-10 w-10 mx-auto text-muted-foreground mb-2" />
                    <p className="text-sm font-medium">Drop audio files or click to browse</p>
                    <p className="text-xs text-muted-foreground mt-1">
                      .wav, .mp3, .flac, .ogg, .m4a (2–30 sec each)
                    </p>
                  </>
                )}
              </div>
            </TabsContent>
          </Tabs>
        </motion.div>

        <motion.div
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.3, delay: 0.1 }}
          className={cn(CARD_CLASS, 'p-5')}
        >
          <Label className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
            Text for TTS
          </Label>
          <Tabs
            value={textInputMode}
            onValueChange={(v) => setTextInputMode(v as 'type' | 'txt' | 'json')}
            className="mt-3"
          >
            <TabsList className="grid w-full grid-cols-3 h-9 rounded-full bg-muted p-1 text-xs">
              <TabsTrigger value="type" className="rounded-full gap-1.5">
                <FileText className="h-3.5 w-3.5" />
                Type / Paste
              </TabsTrigger>
              <TabsTrigger value="txt" className="rounded-full gap-1.5">
                Upload TXT
              </TabsTrigger>
              <TabsTrigger value="json" className="rounded-full gap-1.5">
                <FileJson className="h-3.5 w-3.5" />
                JSON
              </TabsTrigger>
            </TabsList>

            <TabsContent value="type" className="mt-4 space-y-2">
              <Textarea
                id="text-lines"
                placeholder="What is your name?&#10;Where are you from?&#10;What do you do for a living?"
                rows={5}
                value={textLines}
                onChange={(e) => setTextLines(e.target.value)}
                className="rounded-xl resize-none"
              />
              <p className="text-xs text-muted-foreground flex items-center gap-2">
                {lineCount > 0 && (
                  <span className="font-medium text-foreground">{lineCount} lines</span>
                )}
                Each line = one output audio file per source voice.
              </p>
            </TabsContent>

            <TabsContent value="txt" className="mt-4 space-y-2">
              <div
                role="button"
                tabIndex={0}
                onDragOver={(e) => {
                  preventDefault(e);
                  setTxtDropActive(true);
                }}
                onDragLeave={(e) => {
                  preventDefault(e);
                  setTxtDropActive(false);
                }}
                onDrop={handleTxtDrop}
                onClick={() => textFileRef.current?.click()}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    textFileRef.current?.click();
                  }
                }}
                className={cn(
                  'border-2 border-dashed rounded-xl p-4 text-center transition-colors cursor-pointer',
                  txtDropActive
                    ? 'border-accent bg-accent/10'
                    : 'border-muted-foreground/30 hover:border-muted-foreground/50 hover:bg-muted/30',
                )}
              >
                <input
                  ref={textFileRef}
                  type="file"
                  accept=".txt"
                  className="sr-only"
                  onChange={handleTextFileChange}
                />
                {hasTextFile && textFileName ? (
                  <p className="text-sm font-medium truncate">{textFileName}</p>
                ) : (
                  <p className="text-sm text-muted-foreground">
                    Drop .txt or click to upload (one phrase per line)
                  </p>
                )}
              </div>
            </TabsContent>

            <TabsContent value="json" className="mt-4 space-y-3">
              <div className="space-y-2">
                <Input
                  ref={jsonFileRef}
                  type="file"
                  accept=".json,application/json"
                  onChange={handleJsonFileChange}
                  className="rounded-xl max-w-xs"
                />
                <p className="text-xs text-muted-foreground">
                  <code className="rounded bg-muted px-1 py-0.5">wav</code> = output
                  filename; <code className="rounded bg-muted px-1 py-0.5">text</code> =
                  sent to TTS.
                </p>
              </div>
              <div className="rounded-lg border border-muted bg-muted/30 overflow-hidden">
                <button
                  type="button"
                  onClick={() => setJsonSchemaOpen((o) => !o)}
                  className="w-full flex items-center justify-between gap-2 p-3 text-left text-sm font-medium hover:bg-muted/50 transition-colors"
                >
                  <span className="flex items-center gap-2">
                    <FileJson className="h-4 w-4" />
                    Example JSON schema
                  </span>
                  {jsonSchemaOpen ? (
                    <ChevronUp className="h-4 w-4 shrink-0" />
                  ) : (
                    <ChevronDown className="h-4 w-4 shrink-0" />
                  )}
                </button>
                <AnimatePresence initial={false}>
                  {jsonSchemaOpen && (
                    <motion.div
                      initial={{ height: 0, opacity: 0 }}
                      animate={{ height: 'auto', opacity: 1 }}
                      exit={{ height: 0, opacity: 0 }}
                      transition={{ duration: 0.2 }}
                      className="overflow-hidden"
                    >
                      <pre className="overflow-x-auto border-t border-muted bg-background p-4 text-xs">
                        <code className="text-foreground">{EXAMPLE_JSON}</code>
                      </pre>
                    </motion.div>
                  )}
                </AnimatePresence>
              </div>
            </TabsContent>
          </Tabs>
        </motion.div>

        <motion.div
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.3, delay: 0.15 }}
          className={cn(CARD_CLASS, 'p-5')}
        >
          <Label className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
            Settings
          </Label>
          <div className="mt-3 flex flex-wrap gap-4 sm:gap-6">
            <div className="space-y-1.5 min-w-[140px]">
              <Label className="text-xs">Language</Label>
              <Select value={language} onValueChange={setLanguage}>
                <SelectTrigger className="rounded-xl w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {LANGUAGE_OPTIONS.map((opt) => (
                    <SelectItem key={opt.value} value={opt.value}>
                      {opt.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1.5 min-w-[140px]">
              <Label className="text-xs">Whisper model</Label>
              <Select value={sttModel} onValueChange={setSttModel}>
                <SelectTrigger className="rounded-xl w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {STT_MODELS.map((m) => (
                    <SelectItem key={m.value} value={m.value}>
                      {m.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1.5 flex-1 min-w-[200px]">
              <Label htmlFor="output-zip-name" className="text-xs">
                Output ZIP (optional)
              </Label>
              <Input
                id="output-zip-name"
                placeholder="e.g. my-voices.zip"
                value={outputZipName}
                onChange={(e) => setOutputZipName(e.target.value)}
                className="rounded-xl"
              />
            </div>
          </div>
        </motion.div>

        <motion.div
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.3, delay: 0.2 }}
          className={cn(CARD_CLASS, 'p-5')}
        >
          <Label className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
            Run
          </Label>
          <div className="mt-4">
            <AnimatePresence mode="wait">
              {!batchId ? (
                <motion.div
                  key="idle"
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  exit={{ opacity: 0 }}
                  className="flex justify-center"
                >
                  <Button
                    onClick={handleRun}
                    disabled={!canRun}
                    size="lg"
                    className="rounded-full h-12 px-8 bg-accent text-accent-foreground hover:bg-accent/90 hover:scale-[1.02] active:scale-[0.98] transition-transform"
                  >
                    {isStarting ? (
                      <>
                        <Loader2 className="mr-2 h-5 w-5 animate-spin" />
                        Starting...
                      </>
                    ) : (
                      <>
                        <Play className="mr-2 h-5 w-5" />
                        Run batch clone
                      </>
                    )}
                  </Button>
                </motion.div>
              ) : (
                <motion.div
                  key="status"
                  initial={{ opacity: 0, y: 8 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: -8 }}
                  transition={{ duration: 0.25 }}
                  className="space-y-4"
                >
                  {status === 'processing' && (
                    <div className="space-y-3">
                      <div className="flex items-center gap-2 text-sm">
                        <Loader2 className="h-5 w-5 animate-spin shrink-0 text-accent" />
                        <span className="text-muted-foreground">
                          Cloning and generating…
                        </span>
                      </div>
                      <Progress value={progressPercent} className="h-2 rounded-full" />
                      <p className="text-xs text-muted-foreground">
                        Source {progress?.current_source ?? 0} of {progress?.total_sources ?? 1}
                        {' · '}
                        Line {progress?.current_line ?? 0} of {progress?.total_lines ?? 0}
                      </p>
                    </div>
                  )}

                  {status === 'error' && (
                    <div className="rounded-xl border border-destructive/30 bg-destructive/5 p-4 space-y-3">
                      <div className="flex items-center gap-2 text-destructive font-medium">
                        <AlertCircle className="h-5 w-5 shrink-0" />
                        Error
                      </div>
                      <p className="text-sm text-muted-foreground">{error}</p>
                      <Button variant="outline" size="sm" onClick={reset}>
                        Try again
                      </Button>
                    </div>
                  )}

                  {status === 'complete' && (
                    <div className="space-y-4">
                      <div className="flex items-center gap-2 text-green-600 dark:text-green-400 font-medium">
                        <CheckCircle2 className="h-5 w-5 shrink-0" />
                        Complete!
                      </div>
                      <div className="flex flex-wrap gap-2">
                        <Button
                          onClick={handleDownloadZip}
                          className="rounded-full bg-accent hover:bg-accent/90 hover:scale-[1.02] transition-transform"
                        >
                          <Download className="mr-2 h-4 w-4" />
                          Download ZIP
                        </Button>
                        <Button variant="outline" size="sm" className="rounded-full" onClick={reset}>
                          New batch
                        </Button>
                      </div>
                    </div>
                  )}
                </motion.div>
              )}
            </AnimatePresence>
          </div>
        </motion.div>
      </div>
    </div>
  );
}
